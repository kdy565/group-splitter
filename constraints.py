# constraints.py
import pandas as pd
from typing import Dict, List, Tuple, Set, Iterable
from collections import defaultdict, deque

ALLOWED_TYPES = {"together", "apart"}
ALLOWED_STRENGTH = {"hard", "soft"}

def process_constraints(file_path: str) -> Dict[str, List[Tuple[Set[str], str]]]:
    """
    제약 파일을 읽고, 조건별로 구조화합니다.
    예: {
        'must_together': [({'A','B'}, 'hard'), ({'C','D'}, 'soft')],
        'cannot_together': [({'E','F'}, 'hard')],
        'prefer_together': [...],
        'prefer_apart': [...]
    }
    """

    df = pd.read_csv(file_path)

    constraints: Dict[str, List[Tuple[Set[str], str]]] = {
        "together": [],
        "apart": [],
    }

    for _, row in df.iterrows():
        ctype = str(row["type"]).strip().lower()
        id1 = str(row["id1"]).strip()
        id2 = str(row["id2"]).strip()
        strength = str(row.get("strength", "soft")).strip().lower()

        if ctype not in ALLOWED_TYPES:
            raise ValueError(f"알 수 없는 type: {ctype}")
        if strength not in ALLOWED_STRENGTH:
            raise ValueError(f"알 수 없는 strength: {strength}")
        if not id1 or not id2:
            continue  # 빈 칸은 스킵

        ids_set = {id1, id2}
        constraints[ctype].append((ids_set, strength))

    return constraints


def build_hard_clusters(
    people_ids: Iterable[str],
    constraints: Dict[str, List[Tuple[Set[str], str]]],
) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """
    together:hard 쌍들로만 그래프를 만들고, 연결요소를 클러스터로 반환.
    returns: (id_to_cluster, cluster_to_ids)
    """
    ids = set(people_ids)
    adj: Dict[str, Set[str]] = defaultdict(set)

    # 그래프 구성: together:hard만 간선으로 추가
    for pair, strength in constraints.get("together", []):
        if strength != "hard" or len(pair) != 2:
            continue
        a, b = tuple(pair)
        if a in ids and b in ids:
            adj[a].add(b)
            adj[b].add(a)

    # 방문표시 + 연결요소 찾기(DFS/BFS)
    id_to_cluster: Dict[str, str] = {}
    cluster_to_ids: Dict[str, Set[str]] = {}
    visited: Set[str] = set()

    for v in ids:
        if v in visited:
            continue
        # v를 시작으로 컴포넌트 탐색
        comp = set()
        q = deque([v])
        visited.add(v)
        comp.add(v)
        while q:
            cur = q.popleft()
            for nxt in adj[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    comp.add(nxt)
                    q.append(nxt)
        # 대표 id(작은 문자열)로 cluster_id 부여
        cluster_id = min(comp)
        for pid in comp:
            id_to_cluster[pid] = cluster_id
        cluster_to_ids[cluster_id] = comp

    # 고립 노드(간선 없는 id)도 위 루프에서 컴포넌트 1개로 처리됨
    return id_to_cluster, cluster_to_ids

def validate_hard_contradictions_simple(
    constraints: Dict[str, List[Tuple[Set[str], str]]],
    id_to_cluster: Dict[str, str],
) -> List[Dict]:
    """
    하드 모순 검증 (간단판):
    1) 동일 쌍에 대해 together:hard & apart:hard 동시 존재 → 모순
    2) apart:hard 쌍이 같은 클러스터에 속함 → 모순
    """
    contradictions: List[Dict] = []

    together_hard = set()
    apart_hard = set()

    for pair, strength in constraints.get("together", []):
        if strength == "hard" and len(pair) == 2:
            together_hard.add(frozenset(pair))

    for pair, strength in constraints.get("apart", []):
        if strength == "hard" and len(pair) == 2:
            apart_hard.add(frozenset(pair))

    # (1) 동일 쌍 hard 충돌
    for s in (together_hard & apart_hard):
        a, b = tuple(s)
        contradictions.append({
            "type": "hard_conflict_same_pair",
            "ids": (a, b),
            "reason": "same pair requested both together:hard and apart:hard",
        })

    # (2) apart:hard가 같은 하드-클러스터 내부인지 확인
    for s in apart_hard:
        a, b = tuple(s)
        ca, cb = id_to_cluster.get(a), id_to_cluster.get(b)
        if ca is not None and cb is not None and ca == cb:
            contradictions.append({
                "type": "hard_conflict_within_cluster",
                "ids": (a, b),
                "reason": "apart:hard pair lies within the same together:hard cluster",
            })

    return contradictions

def check_hard_constraints_satisfied_simple(
    teams: List[dict],
    id_to_cluster: Dict[str, str],
    constraints: Dict[str, List[Tuple[Set[str], str]]],
) -> Tuple[bool, List[Dict]]:
    """
    teams가 하드 제약(together:hard, apart:hard)을 만족하는지 간단 검사.
    - together:hard 클러스터 구성원이 모두 같은 팀인지
    - apart:hard 쌍이 같은 팀에 있지 않은지
    """
    # 멤버→팀 매핑
    member_to_team: Dict[str, str] = {}
    for t in teams:
        tid = t["team_id"]
        for m in t.get("members", []):
            member_to_team[m["id"]] = tid

    violations: List[Dict] = []

    # (A) together:hard 클러스터 검사
    cluster_to_members: Dict[str, List[str]] = defaultdict(list)
    for mid, cid in id_to_cluster.items():
        cluster_to_members[cid].append(mid)

    for cid, members in cluster_to_members.items():
        team_ids = {member_to_team.get(mid) for mid in members if mid in member_to_team}
        missing = [mid for mid in members if mid not in member_to_team]
        if missing:
            violations.append({
                "type": "unassigned_member_in_cluster",
                "cluster_id": cid,
                "members": missing,
                "reason": "hard-together cluster member is unassigned",
            })
        # 팀이 2개 이상이면 분할 배치
        team_ids = {t for t in team_ids if t is not None}
        if len(team_ids) > 1:
            violations.append({
                "type": "together_hard_split",
                "cluster_id": cid,
                "teams": list(team_ids),
                "members": members,
                "reason": "hard-together cluster split across teams",
            })

    # (B) apart:hard 검사 (ID 레벨 그대로)
    for pair, strength in constraints.get("apart", []):
        if strength != "hard" or len(pair) != 2:
            continue
        a, b = tuple(pair)
        ta, tb = member_to_team.get(a), member_to_team.get(b)
        if ta is None or tb is None:
            continue  # 배정 전이거나 누락: 정책상 스킵
        if ta == tb:
            violations.append({
                "type": "apart_hard_same_team",
                "ids": (a, b),
                "team_id": ta,
                "reason": "hard-apart pair on the same team",
            })

    return (len(violations) == 0), violations
