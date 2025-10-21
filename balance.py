# balance.py
from __future__ import annotations

import math
import random
from statistics import pstdev
from typing import Dict, List, Tuple, Set
from collections import defaultdict, Counter

from constraints import build_hard_clusters, check_hard_constraints_satisfied_simple
from allocation import allocate_teams


# =========================
# 기본 유틸
# =========================
def _teams_deepcopy(teams: List[dict]) -> List[dict]:
    """teams 구조 얕은 복사 (member dict는 그대로 참조)"""
    new = []
    for t in teams:
        new.append({
            "team_id": t["team_id"],
            "members": list(t["members"]),
            "group_count": defaultdict(int, dict(t.get("group_count", {}))),
        })
    return new

def _recompute_group_counts(teams: List[dict], group_col: str):
    """teams의 group_count를 멤버 기준으로 재계산"""
    for t in teams:
        gc = defaultdict(int)
        for m in t["members"]:
            gc[m[group_col]] += 1
        t["group_count"] = gc

def _team_sizes(teams: List[dict]) -> List[int]:
    return [len(t["members"]) for t in teams]

def _cluster_maps(id2c: Dict[str, str]) -> Dict[str, List[str]]:
    """cluster_id -> [member_id, ...]"""
    cl: Dict[str, List[str]] = defaultdict(list)
    for pid, cid in id2c.items():
        cl[cid].append(pid)
    return cl

def _commit_move(teams: List[dict], src_idx: int, dst_idx: int, unit_member_ids: List[str], group_col: str):
    """src->dst로 unit_member_ids(같은 클러스터 단위) 이동 + group_count 증분 갱신"""
    src = teams[src_idx]["members"]
    dst = teams[dst_idx]["members"]

    uid_set = set(unit_member_ids)
    moving, remain = [], []
    delta_src = Counter()
    delta_dst = Counter()

    for m in src:
        if m["id"] in uid_set:
            moving.append(m)
            delta_src[m[group_col]] -= 1
            delta_dst[m[group_col]] += 1
        else:
            remain.append(m)

    teams[src_idx]["members"] = remain
    teams[dst_idx]["members"].extend(moving)

    for g, d in delta_src.items():
        teams[src_idx]["group_count"][g] += d
    for g, d in delta_dst.items():
        teams[dst_idx]["group_count"][g] += d


# =========================
# 하드 제약/금지쌍 보조
# =========================
def _apart_hard_pairs_to_cluster_level(
    constraints: Dict[str, List[Tuple[Set[str], str]]],
    id_to_cluster: Dict[str, str],
) -> Set[frozenset]:
    """apart:hard (id,id) → (cluster,cluster) 쌍으로 승격"""
    pairs_c: Set[frozenset] = set()
    for pair, strength in constraints.get("apart", []):
        if strength != "hard" or len(pair) != 2:
            continue
        a, b = tuple(pair)
        ca, cb = id_to_cluster.get(a), id_to_cluster.get(b)
        if ca is None or cb is None or ca == cb:
            # 사전 검증에서 처리되므로 여기서는 skip
            continue
        pairs_c.add(frozenset((ca, cb)))
    return pairs_c

def _clusters_and_apart_pairs(teams: List[dict],
                              constraints: Dict[str, List[Tuple[Set[str], str]]]
                              ) -> Tuple[Dict[str, str], Set[frozenset]]:
    """현재 teams 기준 cluster 맵 + apart:hard 클러스터쌍 추출"""
    all_ids = [m["id"] for T in teams for m in T["members"]]
    id2c, _ = build_hard_clusters(all_ids, constraints)
    apart_c = _apart_hard_pairs_to_cluster_level(constraints, id2c)
    return id2c, apart_c

def _violates_apart_hard(team_member_ids: Set[str],
                         incoming_cluster_ids: Set[str],
                         id2c: Dict[str, str],
                         apart_hard_pairs_c: Set[frozenset]) -> bool:
    """team에 incoming 클러스터들을 추가했을 때 apart:hard 위반 여부"""
    team_clusters = {id2c[mid] for mid in team_member_ids if mid in id2c}
    for nc in incoming_cluster_ids:
        for tc in team_clusters:
            if frozenset((nc, tc)) in apart_hard_pairs_c:
                return True
    return False


# =========================
# 그룹 하한/상한 (학년)
# =========================
def _group_bounds(people_df, K: int, group_col: str = "group") -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    각 그룹 g의 (하한 lb_g, 상한 ub_g) 반환
      - lb_g: Gg >= K 이면 1, 아니면 0   (모든 팀에 최소 1명 요구 가능한 경우만 강제)
      - ub_g: ceil(Gg / K)
    """
    counts = Counter(people_df[group_col].astype(str).tolist())
    lb, ub = {}, {}
    for g, Gg in counts.items():
        lb[g] = 1 if Gg >= K else 0
        ub[g] = max(1, math.ceil(Gg / K))
    return lb, ub


# =========================
# A) 학년 최소 1명 (가능할 때) 강제
# =========================
def adjust_group_min_presence(teams: List[dict],
                              people_df,
                              constraints: Dict[str, List[Tuple[Set[str], str]]],
                              K: int,
                              group_col: str = "group",
                              seed: int = 42,
                              max_iters: int = 400) -> List[dict]:
    rng = random.Random(seed)
    teams = _teams_deepcopy(teams)
    _recompute_group_counts(teams, group_col)

    id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
    c2m = _cluster_maps(id2c)
    lb, ub = _group_bounds(people_df, K, group_col)

    def cluster_is_pure(cid: str, g: str) -> bool:
        ids = c2m[cid]
        for t in teams:
            for m in t["members"]:
                if m["id"] in ids and m[group_col] != g:
                    return False
        return True

    it = 0
    while it < max_iters:
        # 부족 (lb=1인데 0인 팀)
        needs: List[Tuple[int, str]] = []
        for ti, t in enumerate(teams):
            for g, req in lb.items():
                if req == 1 and t["group_count"].get(g, 0) < 1:
                    needs.append((ti, g))
        if not needs:
            break

        progressed = False
        rng.shuffle(needs)
        for dst_idx, g in needs:
            # donor: g를 lb 이상 보유하고 있는 팀 중 cur_g > lb[g]
            donors = []
            for si, st in enumerate(teams):
                if si == dst_idx:
                    continue
                cur_g = st["group_count"].get(g, 0)
                if cur_g > lb[g]:
                    donors.append(si)
            if not donors:
                continue

            # donor에서 순수 g 클러스터 하나 이동
            rng.shuffle(donors)
            moved = False
            for src_idx in donors:
                src_mems = teams[src_idx]["members"]
                g_ids = [m["id"] for m in src_mems if m.get(group_col) == g]
                cand_cids = []
                seen = set()
                for mid in g_ids:
                    cid = id2c.get(mid)
                    if cid and cid not in seen and cluster_is_pure(cid, g):
                        seen.add(cid)
                        cand_cids.append(cid)

                dst_ids = {m["id"] for m in teams[dst_idx]["members"]}
                for cid in cand_cids:
                    unit = list(c2m[cid])
                    # apart 위반?
                    if _violates_apart_hard(dst_ids, {cid}, id2c, apart_c):
                        continue
                    # ub도 초과하지 않게
                    cur_dst_g = teams[dst_idx]["group_count"].get(g, 0)
                    if cur_dst_g + len(unit) > ub[g]:
                        continue

                    _commit_move(teams, src_idx, dst_idx, unit, group_col)
                    progressed = moved = True
                    _recompute_group_counts(teams, group_col)
                    id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
                    c2m = _cluster_maps(id2c)
                    break
                if moved:
                    break
        if not progressed:
            break
        it += 1

    return teams


# =========================
# B) 학년 분산 (cap = ceil(Gg/K)) 강제
# =========================
def adjust_group_dispersion(teams: List[dict],
                            people_df,
                            constraints: Dict[str, List[Tuple[Set[str], str]]],
                            K: int,
                            group_col: str = "group",
                            seed: int = 42,
                            max_iters: int = 400) -> List[dict]:
    rng = random.Random(seed)
    teams = _teams_deepcopy(teams)
    _recompute_group_counts(teams, group_col)

    id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
    c2m = _cluster_maps(id2c)
    _, ub = _group_bounds(people_df, K, group_col)

    def over_list() -> List[Tuple[int, str, int]]:
        overs = []
        for ti, t in enumerate(teams):
            for g, cap in ub.items():
                cur = t["group_count"].get(g, 0)
                if cur > cap:
                    overs.append((ti, g, cur - cap))
        overs.sort(key=lambda x: -x[2])  # over 큰 순
        return overs

    def cluster_is_pure(cid: str, g: str) -> bool:
        ids = c2m[cid]
        for t in teams:
            for m in t["members"]:
                if m["id"] in ids and m[group_col] != g:
                    return False
        return True

    it = 0
    while it < max_iters:
        overs = over_list()
        if not overs:
            break

        progressed = False
        for src_idx, g, over in overs:
            if over <= 0:
                continue

            # 목적지 후보: 해당 g가 cap 이하가 되는 팀들
            dst_candidates = []
            for ti, t in enumerate(teams):
                if ti == src_idx:
                    continue
                cur = t["group_count"].get(g, 0)
                if cur < ub[g]:
                    dst_candidates.append(ti)
            if not dst_candidates:
                continue

            # src에서 g-순수 클러스터들을 작은 것부터
            src_mems = teams[src_idx]["members"]
            g_ids = [m["id"] for m in src_mems if m.get(group_col) == g]
            seen, cand_cids = set(), []
            for mid in g_ids:
                cid = id2c.get(mid)
                if cid and cid not in seen and cluster_is_pure(cid, g):
                    seen.add(cid)
                    cand_cids.append(cid)

            cand_cids.sort(key=lambda c: len(c2m[c]))  # 작은 덩어리 우선

            for cid in cand_cids:
                unit = list(c2m[cid])
                # 목적지 중 apart 위반/ub 초과 없는 팀
                random.shuffle(dst_candidates)
                chosen = None
                for dst_idx in dst_candidates:
                    dst_ids = {m["id"] for m in teams[dst_idx]["members"]}
                    if _violates_apart_hard(dst_ids, {cid}, id2c, apart_c):
                        continue
                    if teams[dst_idx]["group_count"].get(g, 0) + len(unit) > ub[g]:
                        continue
                    chosen = dst_idx
                    break
                if chosen is None:
                    continue

                _commit_move(teams, src_idx, chosen, unit, group_col)
                progressed = True
                _recompute_group_counts(teams, group_col)
                id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
                c2m = _cluster_maps(id2c)

            # 한 번의 overs 루프에서 더 진행할 수 있는 만큼 진행
        if not progressed:
            break
        it += 1

    return teams


# =========================
# (선택) C) 총원 가벼운 조정 (하한/상한/하드 절대 불가침)
# =========================
def adjust_team_sizes_light(teams: List[dict],
                            people_df,
                            constraints: Dict[str, List[Tuple[Set[str], str]]],
                            K: int,
                            group_col: str = "group",
                            seed: int = 42,
                            max_iters: int = 200) -> List[dict]:
    rng = random.Random(seed)
    teams = _teams_deepcopy(teams)
    _recompute_group_counts(teams, group_col)

    lb, ub = _group_bounds(people_df, K, group_col)
    id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
    c2m = _cluster_maps(id2c)

    def safe_if_move(src_idx: int, dst_idx: int, cid: str) -> bool:
        """LB/UB/금지쌍 모두 안전하면 True"""
        ids = set(c2m[cid])
        # apart
        dst_ids = {m["id"] for m in teams[dst_idx]["members"]}
        if _violates_apart_hard(dst_ids, {cid}, id2c, apart_c):
            return False
        # LB: 이동으로 src의 어떤 g가 1 미만 되면 안 됨 (lb[g]==1인 경우)
        # UB: 이동으로 dst의 어떤 g가 cap 초과되면 안 됨
        gdelta = Counter()
        for m in teams[src_idx]["members"]:
            if m["id"] in ids:
                gdelta[m[group_col]] -= 1
        for m in teams[dst_idx]["members"]:
            pass  # dst는 증가만 아래서 반영
        # dst 증가 반영
        for pid in ids:
            # pid의 group 알아내기
            for m in teams[src_idx]["members"]:
                if m["id"] == pid:
                    gdelta[m[group_col]] += 1
                    break
        # 검증
        for g, d in gdelta.items():
            src_cur = teams[src_idx]["group_count"].get(g, 0)
            dst_cur = teams[dst_idx]["group_count"].get(g, 0)
            if d < 0 and lb.get(g, 0) == 1 and src_cur + d < 1:
                return False
            if d > 0 and dst_cur + d > ub.get(g, 10**9):
                return False
        return True

    it = 0
    while it < max_iters:
        sizes = _team_sizes(teams)
        max_sz, min_sz = max(sizes), min(sizes)
        if max_sz - min_sz <= 1:
            break
        src_idx = sizes.index(max_sz)
        dst_idx = sizes.index(min_sz)

        # src에서 이동 가능한 '작은' 클러스터 찾기 (그룹 순수 여부 상관 X)
        src_ids = [m["id"] for m in teams[src_idx]["members"]]
        rng.shuffle(src_ids)
        tried = False
        moved = False
        seen_c = set()
        for mid in src_ids:
            cid = id2c.get(mid)
            if not cid or cid in seen_c:
                continue
            seen_c.add(cid)
            unit = c2m[cid]
            if len(unit) > max_sz - min_sz:
                continue  # 너무 큰 덩어리는 한 번에 못 옮김
            tried = True
            if not safe_if_move(src_idx, dst_idx, cid):
                continue
            _commit_move(teams, src_idx, dst_idx, list(unit), group_col)
            moved = True
            _recompute_group_counts(teams, group_col)
            id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
            c2m = _cluster_maps(id2c)
            break

        if not tried or not moved:
            # 다른 목적지도 잠깐 시도
            idxs = list(range(len(teams)))
            rng.shuffle(idxs)
            for alt_dst in idxs:
                if alt_dst in (src_idx, dst_idx):
                    continue
                moved2 = False
                seen_c.clear()
                for mid in src_ids:
                    cid = id2c.get(mid)
                    if not cid or cid in seen_c:
                        continue
                    seen_c.add(cid)
                    unit = c2m[cid]
                    if len(unit) > max_sz - min_sz:
                        continue
                    if not safe_if_move(src_idx, alt_dst, cid):
                        continue
                    _commit_move(teams, src_idx, alt_dst, list(unit), group_col)
                    moved2 = True
                    _recompute_group_counts(teams, group_col)
                    id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
                    c2m = _cluster_maps(id2c)
                    break
                if moved2:
                    moved = True
                    break

        if not moved:
            break  # 더 진행 어려움
        it += 1

    return teams


# =========================
# 파이프라인(단일 시드)
# =========================
def balance_teams(teams: List[dict],
                  people_df,
                  constraints: Dict[str, List[Tuple[Set[str], str]]],
                  K: int,
                  group_col: str = "group",
                  seed: int = 42,
                  light_size_balance: bool = True) -> List[dict]:
    """
    우선순위 보존:
      1) 학년 최소 1명 (가능할 때) 강제
      2) 학년 cap(ceil(Gg/K)) 강제
      3) (선택) 총원 가벼운 조정 (하한/상한/하드 불가침)
    """
    t1 = adjust_group_min_presence(teams, people_df, constraints, K, group_col=group_col, seed=seed)
    t2 = adjust_group_dispersion(t1, people_df, constraints, K, group_col=group_col, seed=seed)
    if light_size_balance:
        t3 = adjust_team_sizes_light(t2, people_df, constraints, K, group_col=group_col, seed=seed)
    else:
        t3 = t2

    # 하드/바운드 재검증 (안전망)
    if not _verify_hard_and_group_bounds(t3, people_df, constraints, K, group_col):
        # 혹시 실패하면 이전 단계로 롤백
        return t2
    return t3


# =========================
# 검증 & 점수 & 탐색 (여러 해 중 최선 선택)
# =========================
def _verify_hard_and_group_bounds(teams: List[dict],
                                  people_df,
                                  constraints: Dict[str, List[Tuple[Set[str], str]]],
                                  K: int,
                                  group_col: str = "group") -> bool:
    """하드 제약 + 학년 하한/상한 모두 만족하는지 검사"""
    all_ids = [m["id"] for T in teams for m in T["members"]]
    id2c, _ = build_hard_clusters(all_ids, constraints)
    ok, _viol = check_hard_constraints_satisfied_simple(teams, id2c, constraints)
    if not ok:
        return False

    lb, ub = _group_bounds(people_df, K, group_col)
    for t in teams:
        gc = t["group_count"]
        for g, req in lb.items():
            if req == 1 and gc.get(g, 0) < 1:
                return False
        for g, cap in ub.items():
            if gc.get(g, 0) > cap:
                return False
    return True

def _score_soft(teams: List[dict], gender_col: str = "gender") -> Tuple[float, float, float]:
    """
    소프트 점수: (총원 표준편차, 성비 편차합, 종합점수)
    - 성비는 참고용, 가중치는 낮게 (스왑 없음)
    """
    sizes = [len(t["members"]) for t in teams]
    size_std = pstdev(sizes) if len(sizes) > 1 else 0.0

    all_g = [m.get(gender_col) for T in teams for m in T["members"]]
    n = len(all_g)
    if n == 0:
        gender_err = 0.0
    else:
        p_f = all_g.count("F") / n
        gender_err = 0.0
        for t in teams:
            nn = len(t["members"])
            if nn == 0:
                continue
            target_f = p_f * nn
            cur_f = sum(1 for m in t["members"] if m.get(gender_col) == "F")
            gender_err += abs(cur_f - target_f)

    w_size, w_gender = 1.0, 0.3  # 총원 > 성비
    score = w_size * size_std + w_gender * gender_err
    return size_std, gender_err, score

def build_feasible_once(people_df,
                        constraints: Dict[str, List[Tuple[Set[str], str]]],
                        K: int,
                        seed: int,
                        group_col: str = "group",
                        gender_col: str = "gender",
                        light_size_balance: bool = True) -> List[dict]:
    """
    하나의 시드로 '하드+하한+상한'을 만족하는 해를 생성 (스왑 없음)
    """
    # 1) 1차 배정
    teams0 = allocate_teams(people_df, K, constraints, seed=seed,
                            group_col=group_col, gender_col=gender_col)
    _recompute_group_counts(teams0, group_col)

    # 2) 밸런싱 파이프라인
    teams1 = balance_teams(teams0, people_df, constraints, K,
                           group_col=group_col, seed=seed,
                           light_size_balance=light_size_balance)
    return teams1

def search_best_feasible(people_df,
                         constraints: Dict[str, List[Tuple[Set[str], str]]],
                         K: int,
                         trials: int = 60,
                         base_seed: int = 42,
                         group_col: str = "group",
                         gender_col: str = "gender") -> Tuple[List[dict] | None, Dict]:
    """
    여러 시드로 '하드+하한+상한'을 만족하는 해들을 생성 → 소프트 점수로 최선 선택
    반환: (best_teams, info_dict)
    """
    rng = random.Random(base_seed)
    best = None   # (score, size_std, gender_err, teams, seed)
    pareto: List[Tuple[float, float, List[dict], int]] = []

    for _ in range(trials):
        seed = rng.randint(1, 10**9)
        teams = build_feasible_once(people_df, constraints, K, seed,
                                    group_col=group_col, gender_col=gender_col,
                                    light_size_balance=True)
        if not _verify_hard_and_group_bounds(teams, people_df, constraints, K, group_col):
            continue

        size_std, gender_err, score = _score_soft(teams, gender_col=gender_col)

        if (best is None) or (score < best[0]):
            best = (score, size_std, gender_err, teams, seed)

        # 2목표 파레토 프런티어 간단 유지
        dominated_idx = []
        is_dominated = False
        for i, (ss, ge, _t, _s) in enumerate(pareto):
            if (ss <= size_std and ge <= gender_err) and (ss < size_std or ge < gender_err):
                is_dominated = True
                break
            if (size_std <= ss and gender_err <= ge) and (size_std < ss or gender_err < ge):
                dominated_idx.append(i)
        if not is_dominated:
            for i in reversed(dominated_idx):
                pareto.pop(i)
            pareto.append((size_std, gender_err, teams, seed))

    info = {
        "best_seed": None if best is None else best[4],
        "best_score": None if best is None else best[0],
        "best_size_std": None if best is None else best[1],
        "best_gender_err": None if best is None else best[2],
        "pareto_top3": [
            {"seed": s, "size_std": ss, "gender_err": ge}
            for ss, ge, _t, s in sorted(pareto, key=lambda x: (x[0], x[1]))[:3]
        ],
    }
    return (None if best is None else best[3], info)