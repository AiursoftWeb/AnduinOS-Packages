#!/usr/bin/env python3
"""
Find redundant transitive dependencies across AnduinOS packages.

Levels: 1=Suggest, 2=Recommend, 3=Dependency.

Rule: A's direct declaration of C at level Sa is REDUNDANT if there exists
a transitive path from A to C (through A's OTHER direct deps, not using the
A→C edge itself) where every edge on the path has level ≥ Sa.
"""

import re
import sys
from pathlib import Path
from collections import defaultdict, deque

ROOT = Path(__file__).resolve().parent.parent
LEVEL_MAP = {"Suggest": 1, "Recommend": 2, "Dependency": 3}
LEVEL_SHORT = {1: "S", 2: "R", 3: "D"}


def parse_aosproj(filepath: Path):
    text = filepath.read_text()
    name = re.search(r"<PackageName>(.*?)</PackageName>", text)
    if not name:
        return None, {}
    pkg = name.group(1)
    deps = {}
    for m in re.finditer(
        r'<(Dependency|Recommend|Suggest)\s+Include="([^"]+)"', text
    ):
        tag, dep_str = m.group(1), m.group(2)
        level = LEVEL_MAP[tag]
        for part in dep_str.split("|"):
            d = part.strip()
            deps[d] = max(deps.get(d, 0), level)
    return pkg, deps


def best_path(start, target, graph, exclude):
    """
    BFS to find a best (highest-minimum-edge) path from start to target
    without using the excluded direct edge.  Returns (strength, [nodes])
    or (0, []) when no path exists.
    """
    if start not in graph:
        return 0, []

    # BFS with path tracking: (node, path_list, current_min_strength)
    q = deque()
    q.append((start, [start], 4))
    best_strength = 0
    best_path_list = []

    while q:
        node, path, cur_min = q.popleft()
        if node == target and len(path) > 1:
            if cur_min > best_strength:
                best_strength = cur_min
                best_path_list = path
            continue

        for neighbor, edge_lvl in sorted(graph.get(node, {}).items()):
            if (node, neighbor) == exclude:
                continue
            if neighbor in path:
                continue  # avoid cycles
            new_min = min(cur_min, edge_lvl)
            q.append((neighbor, path + [neighbor], new_min))

    return best_strength, best_path_list


def format_path(pth, graph):
    """Render a node list with edge levels: A -[R]-> B -[D]-> C."""
    parts = []
    for i in range(len(pth) - 1):
        a, b = pth[i], pth[i + 1]
        lvl = LEVEL_SHORT.get(graph[a][b], "?")
        parts.append(f"{a} -[{lvl}]→")
    parts.append(pth[-1])
    return " ".join(parts)


def main():
    all_pkgs = {}
    raw_deps = {}

    for f in sorted(ROOT.glob("*/*.aosproj")):
        name, deps = parse_aosproj(f)
        if name:
            all_pkgs[name] = deps
            raw_deps[name] = deps

    graph = defaultdict(dict)
    for pkg in all_pkgs:
        graph[pkg]  # ensure leaf packages exist in graph
    for pkg, deps in raw_deps.items():
        for dep, level in deps.items():
            if dep in all_pkgs:
                graph[pkg][dep] = level

    redundants = []
    for pkg, direct_deps in sorted(graph.items()):
        for dep_cand, direct_lvl in sorted(direct_deps.items()):
            strength, pth = best_path(
                pkg, dep_cand, graph, exclude=(pkg, dep_cand)
            )
            if strength >= direct_lvl:
                redundants.append((pkg, dep_cand, direct_lvl, strength, pth))

    if not redundants:
        print("✅ No redundant transitive dependencies found.")
        return 0

    print(f"=== {len(redundants)} redundant transitive dependencies found ===\n")
    for pkg, redundant, dl, tl, pth in redundants:
        dn = LEVEL_SHORT.get(dl, "?")
        tn = LEVEL_SHORT.get(tl, "?")
        print(
            f"  {pkg} → {redundant}  "
            f"[direct: {dn}({dl}), transitive: {tn}({tl}) ≥ {dn}({dl})]"
        )
        print(f"        {format_path(pth, graph)}")
        print()

    return len(redundants)


if __name__ == "__main__":
    sys.exit(min(main(), 1))
