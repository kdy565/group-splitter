# allocation.py
from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict, Counter
import random

import pandas as pd
from constraints import build_hard_clusters

def compute_group_targets(
    df: pd.DataFrame,
    K: int,
    seed: int = 42,
    group_col: str = "group",
) -> Dict[str, List[int]]:
    """
    각 그룹 g에 대해 총원 Gg를 K개 팀으로 floor/나머지 방식으로 분해한
    목표치 벡터를 생성합니다. (잔여는 시드 기반으로 섞어서 분산)
      예) 4학년 10명, K=3 -> 기본 [3,3,3] + 잔여 1명을 임의 팀에 가산 -> [3,3,4]

    Returns
    -------
    targets: Dict[group -> List[length K] of int]
    """
    rng = random.Random(seed)
    targets: Dict[str, List[int]] = {}
    counts = Counter(df["group"].astype(str).tolist())

    for g, Gg in counts.items():
        base = Gg // K
        r = Gg % K
        vec = [base] * K
        idxs = list(range(K))
        rng.shuffle(idxs)
        for i in idxs[:r]:
            vec[i] += 1
        targets[str(g)] = vec
    return targets

def _apart_hard_pairs_to_cluster_level(
    constraints: Dict[str, List[Tuple[Set[str], str]]],
    id_to_cluster: Dict[str, str],
) -> Set[frozenset]:
    """
    apart:hard (id,id) 쌍을 (cluster,cluster) 쌍으로 승격.
    """
    pairs_c: Set[frozenset] = set()
    for pair, strength in constraints.get("apart", []):
        if strength != "hard" or len(pair) != 2:
            continue
        a, b = tuple(pair)
        ca = id_to_cluster.get(a)
        cb = id_to_cluster.get(b)
        if ca is None or cb is None:
            # people에 없는 id는 배정 대상이 아니므로 무시
            continue
        if ca == cb:
            # validate_hard_contradictions_simple에서 이미 검출 되었을 것....
            continue
        pairs_c.add(frozenset((ca, cb)))
    return pairs_c

def _empty_teams(K: int) -> List[dict]:
    return [{"team_id": i + 1, "members": [], "group_count": defaultdict(int)} for i in range(K)]


def allocate_teams(
    df: pd.DataFrame,
    K: int,
    constraints: Dict[str, List[Tuple[Set[str], str]]],
    seed: int = 41,
    group_col: str = "group",
    gender_col: str = "gender",  # 여기선 사용 X (balance 단계에서 사용)
) -> List[dict]:
    """
    하드 제약을 준수하면서 '그룹 목표치'에 최대한 맞추는 1차 배정.
    - together:hard 클러스터를 먼저 큰 것부터 배치
    - apart:hard 클러스터 간 동팀 금지
    - 그룹 목표치 편차가 최소가 되는 팀을 선택

    Returns
    -------
    teams: List[dict]
      예) [{"team_id": 1, "members": [{"id":...,"name":...,"group":...,"gender":...}, ... ]}, ...]
    """
    rng = random.Random(seed)

    # --- 입력 전처리 ---
    # 필요한 컬럼만 뽑고, 문자열화 & 결측 제거(최소 한도)
    cols = ["id", "name", group_col, gender_col]
    use_cols = [c for c in cols if c in df.columns]
    people = (
        df[use_cols]
        .dropna(subset=["id", group_col])  # id와 group은 필수
        .copy()
    )
    people["id"] = people["id"].astype(str).str.strip()
    people[group_col] = people[group_col].astype(str).str.strip()
    if "name" in people.columns:
        people["name"] = people["name"].astype(str).str.strip()
    if gender_col in people.columns:
        people[gender_col] = people[gender_col].astype(str).str.strip()

    # --- 하드 클러스터 구성 ---
    people_ids = people["id"].tolist()
    id_to_cluster, cluster_to_ids = build_hard_clusters(people_ids, constraints)
    apart_hard_pairs_c = _apart_hard_pairs_to_cluster_level(constraints, id_to_cluster)

    # --- 그룹 목표 산정 ---
    group_targets = compute_group_targets(people, K, group_col=group_col, seed=seed)

    # --- 클러스터 단위 엔티티 구성 ---
    # 클러스터마다: 멤버 list, size, 그룹별 카운트
    cluster_entities = []
    people_by_id = {row["id"]: row.to_dict() for _, row in people.iterrows()}

    for cid, members in cluster_to_ids.items():
        members_list = [people_by_id[mid] for mid in members if mid in people_by_id]
        gcount = Counter([m[group_col] for m in members_list])
        cluster_entities.append({
            "cluster_id": cid,
            "members": members_list,
            "size": len(members_list),
            "group_count": gcount,  # Dict[group -> int]
        })

    # 클러스터를 큰 것부터(= 제약이 큰 것부터) 배치
    cluster_entities.sort(key=lambda x: (-x["size"], x["cluster_id"]))

    teams = _empty_teams(K)

    # 팀 내 이미 포함된 '클러스터 집합'을 추적 (apart:hard 충돌 방지용)
    team_clusters: List[Set[str]] = [set() for _ in range(K)]

    # --- 배정 함수: 후보 팀 스코어 계산 ---
    def score_team_after_place(team_idx: int, cent: dict) -> Tuple[int, int]:
        """
        배치 시 해당 팀의 '그룹 편차 증가량'과 '배치 후 팀 규모'를 반환.
        - 1차 기준: 그룹 목표 편차 증가량(작을수록 좋음)
        - 2차 기준: 팀 총원(작을수록 선호; 초반 쏠림 방지)
        """
        t = teams[team_idx]
        # 그룹 편차 증가량 계산
        inc = 0
        for g, addcnt in cent["group_count"].items():
            # 현재 팀의 해당 그룹 인원
            cur = t["group_count"].get(g, 0)
            # 현재 팀의 '그룹 목표' (팀별 목표는 group_targets[g][team_idx])
            tgt = group_targets.get(g, [0] * K)[team_idx]
            # 배치 전 오차 & 배치 후 오차
            before = abs(cur - tgt)
            after = abs((cur + addcnt) - tgt)
            inc += (after - before)
        # 팀 총원
        size_after = len(t["members"]) + cent["size"]
        return inc, size_after

    # --- 메인 배치 루프 ---
    for cent in cluster_entities:
        cid = cent["cluster_id"]

        # (A) apart:hard 충돌을 유발하지 않는 팀 후보 찾기
        feasible_idxs = []
        for ti in range(K):
            # 팀에 이미 배치된 클러스터들과 apart:hard 금지 관계인지 확인
            bad = False
            for other_c in team_clusters[ti]:
                if frozenset((cid, other_c)) in apart_hard_pairs_c:
                    bad = True
                    break
            if not bad:
                feasible_idxs.append(ti)

        if not feasible_idxs:
            # 하드 제약 때문에 어떤 팀에도 둘 수 없다면, 배정 불가
            raise ValueError(
                f"[allocate_teams] No feasible team for cluster {cid} due to apart:hard constraints."
            )

        # (B) 후보 중 스코어 최소 팀 선택 (동점이면 무작위 or team_id 작은 쪽)
        scored = [(score_team_after_place(ti, cent), ti) for ti in feasible_idxs]
        scored.sort(key=lambda x: (x[0][0], x[0][1], x[1]))  # (증가량, 규모, 팀인덱스)
        best_ti = scored[0][1]

        # (C) 실제 배치
        t = teams[best_ti]
        t["members"].extend(cent["members"])
        for g, c in cent["group_count"].items():
            t["group_count"][g] += c
        team_clusters[best_ti].add(cid)

    # --- 반환: teams (balance 단계에서 size/gender 추가 조정 예정) ---
    return teams