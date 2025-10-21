# constraints.py
import re
import pandas as pd
from typing import Dict, List, Tuple, Set, Iterable
from collections import defaultdict, deque

ALLOWED_TYPES = {"together", "apart"}
ALLOWED_STRENGTH = {"hard", "soft"}

_hint_group_pat = re.compile(r"[({]\s*([^)}]+)\s*[)}]")  # (4학년) 또는 {4학년}
_hint_id_pat    = re.compile(r"#([A-Za-z0-9_-]+)")       # #P001 같은 태그
_hint_gender_pat= re.compile(r"\|\s*([MF])\s*$")         # |M, |F

def _build_name_index(people_df: pd.DataFrame) -> Dict[str, List[dict]]:
    """
    이름 -> 후보 인원(행 dict)의 리스트
    """
    idx = defaultdict(list)
    for _, row in people_df.iterrows():
        idx[str(row.get("name","")).strip()].append({
            "id": str(row.get("id","")).strip(),
            "name": str(row.get("name","")).strip(),
            "group": str(row.get("group","")).strip(),
            "gender": str(row.get("gender","")).strip(),
        })
    return idx

def _parse_ref_hint(s: str) -> dict:
    """
    '김도연(4학년)|F#P001' 같은 ref에서 힌트 추출
    returns: {"raw":"김도연(4학년)|F#P001","name":"김도연","hint_group":"4학년","hint_gender":"F","hint_id":"P001"}
    """
    raw = s.strip()
    name = raw
    hint_group = None
    hint_id = None
    hint_gender = None

    m = _hint_group_pat.search(raw)
    if m:
        hint_group = m.group(1).strip()
        name = _hint_group_pat.sub("", name).strip()

    m = _hint_id_pat.search(raw)
    if m:
        hint_id = m.group(1).strip()
        name = _hint_id_pat.sub("", name).strip()

    m = _hint_gender_pat.search(raw)
    if m:
        hint_gender = m.group(1).strip().upper()
        name = _hint_gender_pat.sub("", name).strip()

    name = name.strip().rstrip(",")
    return {"raw": raw, "name": name, "hint_group": hint_group, "hint_id": hint_id, "hint_gender": hint_gender}

def _resolve_ref_to_id(ref: str, people_df: pd.DataFrame, name_index: Dict[str, List[dict]]) -> Tuple[str, List[str]]:
    """
    ref가 ID면 그대로 반환.
    ref가 이름이면 people_df에서 매칭.
    동명이인 시 힌트를 사용, 실패하면 에러 후보 리스트를 반환.
    returns: (id_or_empty, errors)
    """
    if not ref or pd.isna(ref):
        return "", ["empty reference"]

    s = str(ref).strip()

    # 1) 이미 ID 형태로 보이면 그대로
    #   예: P001 처럼 우리 ID 규칙이 명확하면 여기에 정규식 강화 가능
    if re.fullmatch(r"[A-Za-z]\d{3,}", s):
        return s, []

    # 2) 힌트 파싱 및 이름 후보 조회
    hint = _parse_ref_hint(s)
    name = hint["name"]
    cands = name_index.get(name, [])

    # 3) hint_id가 있으면 우선 매칭
    if hint["hint_id"]:
        for c in cands:
            if c["id"] == hint["hint_id"]:
                return c["id"], []
        # 이름 후보에 없더라도 people_df 전체에서 id 우선 검색
        row = people_df.loc[people_df["id"].astype(str).str.strip() == hint["hint_id"]]
        if len(row) == 1:
            return str(row.iloc[0]["id"]).strip(), []
        return "", [f"'{s}' → 지정 ID #{hint['hint_id']}를 people.csv에서 찾지 못함"]

    # 4) 후보 수에 따라 분기
    if len(cands) == 0:
        # (옵션) rapidfuzz로 근사 검색 추천 가능
        return "", [f"'{s}' → 이름을 people.csv에서 찾지 못함"]

    if len(cands) == 1:
        return cands[0]["id"], []

    # 5) 동명이인 → hint_group / hint_gender로 필터링
    filtered = cands
    if hint["hint_group"]:
        filtered = [c for c in filtered if c["group"] == hint["hint_group"]]
    if hint["hint_gender"]:
        filtered = [c for c in filtered if c["gender"].upper() == hint["hint_gender"].upper()]

    if len(filtered) == 1:
        return filtered[0]["id"], []

    # 여전히 복수면 후보 안내
    msg = [f"'{s}' → 동명이인 {len(cands)}명. 힌트가 더 필요합니다."]
    for c in cands:
        msg.append(f"  - {c['name']} (id={c['id']}, group={c['group']}, gender={c['gender']})")
    msg.append("  힌트 예: 김도연(4학년), 김도연|F, 김도연#P001")
    return "", msg


def process_constraints_friendly(file_path: str, people_df: pd.DataFrame) -> Dict[str, List[Tuple[Set[str], str]]]:
    """
    사람 친화 입력(constraints_human.csv: ref1/ref2) → 표준 제약 딕셔너리로 변환.
    컬럼: type, ref1, ref2, strength, notes(옵션)
    """
    df = pd.read_csv(file_path,dtype=str, keep_default_na=False)

    required = {"type", "ref1", "ref2"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"constraints file missing columns: {missing}")

    constraints: Dict[str, List[Tuple[Set[str], str]]] = {"together": [], "apart": []}
    name_index = _build_name_index(people_df)
    errors: List[str] = []

    for i, row in df.iterrows():
        ctype = str(row["type"]).strip().lower()
        strength = str(row.get("strength", "soft")).strip().lower()

        if ctype not in ALLOWED_TYPES:
            errors.append(f"[row {i+2}] unknown type: {ctype}")
            continue
        if strength not in ALLOWED_STRENGTH:
            errors.append(f"[row {i+2}] unknown strength: {strength}")
            continue

        id1, e1 = _resolve_ref_to_id(row.get("ref1",""), people_df, name_index)
        id2, e2 = _resolve_ref_to_id(row.get("ref2",""), people_df, name_index)
        if e1: errors += [f"[row {i+2}] {m}" for m in e1]
        if e2: errors += [f"[row {i+2}] {m}" for m in e2]

        if not id1 or not id2 or id1 == id2:
            errors.append(f"[row {i+2}] invalid pair after resolution: ({id1}, {id2})")
            continue

        constraints[ctype].append(({id1, id2}, strength))

    if errors:
        # 에러를 한 번에 보여주고 중단
        msg = "Constraint resolution errors:\n" + "\n".join(errors)
        raise ValueError(msg)

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
