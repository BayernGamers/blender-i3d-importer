"""Standalone verification harness for i3d_shapes_reader / i3d_shapes_models.

No bpy dependency - runs with plain CPython. Walks every .i3d.shapes file
under a game data directory, decodes all entities (SHAPE + SPLINE/SPLINE_L),
and reports per-version counts, decode failures, and shapes/splines that
left unread_bytes != 0 (a sign of an undiscovered layout difference for
that version - see the FS15/FS17/FS19 support plan).

Usage:
    python tools/scan_shapes.py "<game data dir>"
    python tools/scan_shapes.py "<game data dir>" --verbose

Exit code is 0 if every decoded entity had unread_bytes == 0 and there were
no exceptions, 1 otherwise (useful for a quick pass/fail check in a shell).
"""

import argparse
import sys
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "blender_i3d_importer"))

import i3d_shapes_reader  # noqa: E402
import i3d_shapes_models  # noqa: E402


def scan_file(path: Path, verbose: bool, stats: dict) -> None:
    try:
        sf = i3d_shapes_reader.read_shapes_file(str(path))
    except Exception as e:
        stats["file_failures"].append((path, f"{type(e).__name__}: {e}"))
        if verbose:
            print(f"[FAIL] {path}: {type(e).__name__}: {e}")
            traceback.print_exc()
        return

    version = sf.header.version
    stats["version_counts"][version] += 1

    for entity in sf.entities:
        etype = entity.entity_type.name
        if etype == "SHAPE":
            stats["entity_counts"][(version, "SHAPE")] += 1
            try:
                shape = i3d_shapes_models.parse_shape_entity(entity, version)
            except Exception as e:
                stats["entity_failures"].append(
                    (path, version, "SHAPE", entity.type, f"{type(e).__name__}: {e}"))
                if verbose:
                    print(f"  [FAIL] {path.name} SHAPE (type={entity.type}) "
                          f"v{version}: {type(e).__name__}: {e}")
                continue
            if shape.unread_bytes != 0:
                stats["unread_bytes"].append(
                    (path, version, "SHAPE", shape.name, shape.unread_bytes))
        elif etype in ("SPLINE", "SPLINE_L"):
            stats["entity_counts"][(version, etype)] += 1
            try:
                spline = i3d_shapes_models.parse_spline_entity(entity, version)
            except Exception as e:
                stats["entity_failures"].append(
                    (path, version, etype, entity.type, f"{type(e).__name__}: {e}"))
                if verbose:
                    print(f"  [FAIL] {path.name} {etype} v{version}: "
                          f"{type(e).__name__}: {e}")
                continue
            if spline.unread_bytes != 0:
                stats["unread_bytes"].append(
                    (path, version, etype, spline.name, spline.unread_bytes))
        else:
            stats["entity_counts"][(version, f"UNKNOWN({entity.type})")] += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", help="Game data directory to walk (recursively) for .i3d.shapes files")
    ap.add_argument("--verbose", action="store_true", help="Print each failure as it's found")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 1

    stats = {
        "version_counts": Counter(),
        "entity_counts": Counter(),
        "file_failures": [],
        "entity_failures": [],
        "unread_bytes": [],
    }

    files = sorted(root.rglob("*.i3d.shapes"))
    print(f"Found {len(files)} .i3d.shapes file(s) under {root}")

    t0 = time.time()
    for i, path in enumerate(files):
        scan_file(path, args.verbose, stats)
        if not args.verbose and (i + 1) % 200 == 0:
            print(f"  ...{i + 1}/{len(files)}")
    elapsed = time.time() - t0

    print(f"\nDone in {elapsed:.1f}s.\n")

    print("Per-version file counts:")
    for version in sorted(stats["version_counts"]):
        print(f"  v{version}: {stats['version_counts'][version]} file(s)")

    print("\nPer-(version, entity-kind) counts:")
    for (version, kind) in sorted(stats["entity_counts"]):
        print(f"  v{version} {kind}: {stats['entity_counts'][(version, kind)]}")

    if stats["file_failures"]:
        print(f"\n{len(stats['file_failures'])} FILE-LEVEL FAILURE(S):")
        for path, msg in stats["file_failures"][:50]:
            print(f"  {path}: {msg}")
        if len(stats["file_failures"]) > 50:
            print(f"  ... and {len(stats['file_failures']) - 50} more")

    if stats["entity_failures"]:
        print(f"\n{len(stats['entity_failures'])} ENTITY DECODE FAILURE(S):")
        by_version = defaultdict(int)
        for path, version, kind, type_int, msg in stats["entity_failures"]:
            by_version[version] += 1
        for version in sorted(by_version):
            print(f"  v{version}: {by_version[version]} failure(s)")
        for path, version, kind, type_int, msg in stats["entity_failures"][:50]:
            print(f"  {path.name} v{version} {kind} (type={type_int}): {msg}")
        if len(stats["entity_failures"]) > 50:
            print(f"  ... and {len(stats['entity_failures']) - 50} more")

    if stats["unread_bytes"]:
        print(f"\n{len(stats['unread_bytes'])} ENTITY/ENTITIES WITH unread_bytes != 0 "
              f"(undiscovered layout difference):")
        by_version = defaultdict(int)
        for path, version, kind, name, n in stats["unread_bytes"]:
            by_version[version] += 1
        for version in sorted(by_version):
            print(f"  v{version}: {by_version[version]} entity/entities")
        for path, version, kind, name, n in stats["unread_bytes"][:50]:
            print(f"  {path.name} v{version} {kind} {name!r}: {n} unread byte(s)")
        if len(stats["unread_bytes"]) > 50:
            print(f"  ... and {len(stats['unread_bytes']) - 50} more")

    ok = not stats["file_failures"] and not stats["entity_failures"] and not stats["unread_bytes"]
    print(f"\n{'PASS' if ok else 'FAIL'}: "
          f"{len(stats['file_failures'])} file failures, "
          f"{len(stats['entity_failures'])} entity failures, "
          f"{len(stats['unread_bytes'])} unread_bytes cases.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
