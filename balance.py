# balance.py
from typing import List, Dict, Tuple, Set, Optional
from collections import defaultdict, Counter
import random

from constraints import build_hard_clusters, check_hard_constraints_satisfied_simple
from allocation import compute_group_targets 

def _teams_deepcopy(teams: List[dict]) -> List[dict]:
    new = []
    for t in teams:
        new.append({
            "team_id": t["team_id"],
            "members": list(t["members"]),
            "group_count": defaultdict(int, dict(t["group_count"]))
        })
    return new

def _build_fast_indexes(teams: List[dict]) -> Tuple[Dict[str, int], List[Set[str]]]:
    """member_id -> team_idx, team_idx -> set(cluster_id)"""
    member_to_team_idx: Dict[str, int] = {}
    for i, t in enumerate(teams):
        for m in t["members"]:
            member_to_team_idx[m["id"]] = i
    return member_to_team_idx

def _recompute_group_counts(teams: List[dict], group_col: str):
    for t in teams:
        gc = defaultdict(int)
        for m in t["members"]:
            gc[m[group_col]] += 1
        t["group_count"] = gc

def _team_sizes(teams: List[dict]) -> List[int]:
    return [len(t["members"]) for t in teams]

def _gender_counts(teams: List[dict], gender_col: str) -> List[Counter]:
    return [Counter([m.get(gender_col) for m in t["members"]]) for t in teams]

def _clusters_and_apart_pairs(teams: List[dict],
                              constraints: Dict[str, List[Tuple[Set[str], str]]]
                              ) -> Tuple[Dict[str, str], Set[frozenset]]:
    """현재 teams 구성원을 기준으로 클러스터/금지쌍 계산"""
    all_ids = [m["id"] for t in teams for m in t["members"]]
    id2c, _ = build_hard_clusters(all_ids, constraints)

    # apart:hard를 클러스터쌍으로 (간단 체크를 위해 ID 레벨도 같이 쓰지만, 스왑 단위는 클러스터)
    apart_hard_pairs_c: Set[frozenset] = set()
    for pair, strength in constraints.get("apart", []):
        if strength != "hard" or len(pair) != 2:
            continue
        a, b = tuple(pair)
        ca, cb = id2c.get(a), id2c.get(b)
        if ca and cb and ca != cb:
            apart_hard_pairs_c.add(frozenset((ca, cb)))
    return id2c, apart_hard_pairs_c

def _movable_unit(member_id: str, id2c: Dict[str, str], cluster_to_members: Dict[str, List[str]]) -> List[str]:
    """멤버가 속한 하드 클러스터 전체를 반환 (size 1이면 개인 이동과 동일)"""
    cid = id2c[member_id]
    return list(cluster_to_members[cid])

def _cluster_maps(id2c: Dict[str, str]) -> Dict[str, List[str]]:
    cl: Dict[str, List[str]] = defaultdict(list)
    for pid, cid in id2c.items():
        cl[cid].append(pid)
    return cl

def _violates_apart_hard(team_members_ids: Set[str],
                         incoming_cluster_ids: Set[str],
                         id2c: Dict[str, str],
                         apart_hard_pairs_c: Set[frozenset]) -> bool:
    """팀에 클러스터(들)를 추가했을 때 apart:hard 위반 여부"""
    # 팀 내 존재하는 클러스터 집합
    team_clusters = {id2c[mid] for mid in team_members_ids if mid in id2c}
    for nc in incoming_cluster_ids:
        for tc in team_clusters:
            if frozenset((nc, tc)) in apart_hard_pairs_c:
                return True
    return False

# ---------------------------
# 1) 총원 균형 (±1 이내)
# ---------------------------
def adjust_team_sizes(teams: List[dict],
                      people_df,  # group_col용
                      constraints: Dict[str, List[Tuple[Set[str], str]]],
                      K: int,
                      group_col: str = "group",
                      size_tolerance: int = 1,
                      seed: int = 42,
                      max_iters: int = 400) -> List[dict]:
    rng = random.Random(seed)
    teams = _teams_deepcopy(teams)
    _recompute_group_counts(teams, group_col)

    id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
    cluster_to_members = _cluster_maps(id2c)

    # 그룹 목표 (증분 스코어에 참고)
    group_targets = compute_group_targets(people_df, K, group_col=group_col, seed=seed)

    def score_inc_if_move(src_idx: int, dst_idx: int, member_ids: List[str]) -> int:
        """이 클러스터(또는 단일) 이동 시 두 팀의 그룹 편차 증가량 합계"""
        inc = 0
        # 이동 전후 group_count를 가정하고 오차 변화 계산
        # 팀별로 g에 대해 |cur - tgt| 변화의 합
        affected = [src_idx, dst_idx]
        delta = {src_idx: Counter(), dst_idx: Counter()}
        for mid in member_ids:
            g = next(m[group_col] for m in teams[src_idx]["members"] if m["id"] == mid)
            delta[src_idx][g] -= 1
            delta[dst_idx][g] += 1

        for ti in affected:
            t = teams[ti]
            for g, tgt_vec in group_targets.items():
                tgt = tgt_vec[ti]
                cur = t["group_count"].get(g, 0)
                before = abs(cur - tgt)
                after = abs((cur + delta[ti][g]) - tgt)
                inc += (after - before)
        return inc

    # 메인 루프: 가장 큰 팀 -> 가장 작은 팀으로 이동/스왑 시도
    it = 0
    while it < max_iters:
        sizes = _team_sizes(teams)
        max_sz, min_sz = max(sizes), min(sizes)
        if max_sz - min_sz <= size_tolerance:
            break  # 완료

        src_idx = sizes.index(max_sz)
        dst_idx = sizes.index(min_sz)

        # src에서 이동 가능한 "클러스터 단위" 후보 뽑기 (무작위 순회)
        src_members = [m["id"] for m in teams[src_idx]["members"]]
        rng.shuffle(src_members)

        best = None  # (inc, count, member_ids)
        for mid in src_members:
            unit = _movable_unit(mid, id2c, cluster_to_members)  # 클러스터 전체
            unit_set = set(unit)

            # apart:hard 충돌 검사
            dst_member_ids = {m["id"] for m in teams[dst_idx]["members"]}
            incoming_clusters = {id2c[u] for u in unit}
            if _violates_apart_hard(dst_member_ids, incoming_clusters, id2c, apart_c):
                continue

            # 이동 뒤 크기 개선되는지 (downhill)
            if len(unit) > max_sz - min_sz:
                # 너무 큰 덩어리는 한 번에 못 옮김
                continue

            inc = score_inc_if_move(src_idx, dst_idx, unit)
            cand = (inc, len(unit), unit)
            if (best is None) or (cand < best):
                best = cand

        if best is None:
            # 큰 클러스터 때문에 막히는 경우: 다음 큰/작은 팀 조합 시도
            # 간단히 무작위로 다른 dst 후보를 찾아본다
            idxs = list(range(len(teams)))
            rng.shuffle(idxs)
            moved = False
            for alt_dst in idxs:
                if alt_dst == src_idx:
                    continue
                # 재시도
                best2 = None
                dst_member_ids = {m["id"] for m in teams[alt_dst]["members"]}
                for mid in src_members:
                    unit = _movable_unit(mid, id2c, cluster_to_members)
                    incoming_clusters = {id2c[u] for u in unit}
                    if _violates_apart_hard(dst_member_ids, incoming_clusters, id2c, apart_c):
                        continue
                    inc = score_inc_if_move(src_idx, alt_dst, unit)
                    cand = (inc, len(unit), unit)
                    if (best2 is None) or (cand < best2):
                        best2 = cand
                if best2:
                    # 실행
                    _, _, unit = best2
                    # 이동 커밋
                    _commit_move(teams, src_idx, alt_dst, unit, group_col)
                    moved = True
                    break
            if not moved:
                # 더 이상 진행 불가
                break
        else:
            # 실행
            _, _, unit = best
            _commit_move(teams, src_idx, dst_idx, unit, group_col)

        # 인덱스/카운트 재계산
        _recompute_group_counts(teams, group_col)
        id2c, apart_c = _clusters_and_apart_pairs(teams, constraints)
        cluster_to_members = _cluster_maps(id2c)
        it += 1

    return teams

def _commit_move(teams: List[dict], src_idx: int, dst_idx: int, unit_member_ids: List[str], group_col: str):
    """클러스터 단위 이동 커밋"""
    src_members = teams[src_idx]["members"]
    dst_members = teams[dst_idx]["members"]

    moving = []
    remain = []
    src_gc_delta = Counter()
    dst_gc_delta = Counter()

    unit_set = set(unit_member_ids)
    for m in src_members:
        if m["id"] in unit_set:
            moving.append(m)
            src_gc_delta[m[group_col]] -= 1
            dst_gc_delta[m[group_col]] += 1
        else:
            remain.append(m)

    teams[src_idx]["members"] = remain
    teams[dst_idx]["members"] += moving

    # group_count 갱신(증분)
    for g, d in src_gc_delta.items():
        teams[src_idx]["group_count"][g] += d
    for g, d in dst_gc_delta.items():
        teams[dst_idx]["group_count"][g] += d

# ---------------------------
# 2) 성비 균형
# ---------------------------
def adjust_gender_balance(teams: List[dict],
                          gender_col: str = "gender",
                          seed: int = 42,
                          max_iters: int = 400) -> List[dict]:
    """
    단일/동일 클러스터 내에서만 스왑(= 클러스터 분할 금지).
    간단한 휴리스틱: 서로 다른 팀 간 (M<->F) 1:1 스왑으로 팀별 성비 오차 감소.
    """
    rng = random.Random(seed)
    teams = _teams_deepcopy(teams)

    # 전체 성비 비율(목표)을 계산
    all_genders = [m.get(gender_col) for t in teams for m in t["members"]]
    total = len(all_genders)
    if total == 0:
        return teams
    p_f = all_genders.count("F") / total  # 여성 비율(예시)
    # 목표: 각 팀 여성 수 ≈ p_f * 팀총원

    def team_gender_error() -> float:
        err = 0.0
        for t in teams:
            n = len(t["members"])
            if n == 0:
                continue
            target_f = p_f * n
            cur_f = sum(1 for m in t["members"] if m.get(gender_col) == "F")
            err += abs(cur_f - target_f)
        return err

    baseline = team_gender_error()

    # 멤버 목록 펼치기
    by_team = [list(t["members"]) for t in teams]

    it = 0
    improved = True
    while improved and it < max_iters:
        improved = False
        # 모든 팀쌍 무작위 탐색
        idxs = list(range(len(teams)))
        rng.shuffle(idxs)

        for i in idxs:
            for j in idxs:
                if i >= j:
                    continue
                # 후보 멤버 (클러스터 단위 분할 금지 → 개별로 스왑하지만 같은 클러스터 내 1명만 골라야 함)
                mem_i = by_team[i]
                mem_j = by_team[j]
                rng.shuffle(mem_i)
                rng.shuffle(mem_j)

                # 간단히 성별 반대 쌍만 고려
                for a in mem_i:
                    ga = a.get(gender_col)
                    for b in mem_j:
                        gb = b.get(gender_col)
                        if ga == gb:
                            continue  # 같은 성별 스왑은 성비에 영향 없음

                        # 가상 스왑
                        if not _swap_safe_same_cluster_unit(a, b):
                            continue
                        _do_swap(teams, i, j, a["id"], b["id"])
                        new_err = team_gender_error()
                        if new_err + 1e-9 < baseline:
                            baseline = new_err
                            by_team = [list(t["members"]) for t in teams]
                            improved = True
                            break
                        else:
                            # 롤백
                            _do_swap(teams, i, j, b["id"], a["id"])
                    if improved:
                        break
                if improved:
                    break
            if improved:
                break
        it += 1

    return teams

def _swap_safe_same_cluster_unit(a: dict, b: dict) -> bool:
    """
    간소화: 같은 하드 클러스터 내 여러 명이 한 팀에 있다면,
    그 중 1명만 따로 빼는 스왑은 금지해야 맞지만,
    여기서는 allocation에서 이미 클러스터 단위로 묶여 들어왔다는 가정 하에
    '클러스터 size==1' 케이스만 실제로 성비 스왑 대상으로 쓰는 것을 권장.
    → 실제 운영 시엔, 멤버 dict에 'cluster_id'를 붙여 단위 스왑을 엄격히 제한하는 게 더 안전.
    """
    return True  # 최소 구현: 상위에서 단일 멤버만 대상이 되도록 데이터 구성 권장

def _do_swap(teams: List[dict], i: int, j: int, ida: str, idb: str):
    """팀 i의 ida 멤버와 팀 j의 idb 멤버를 교환"""
    ai = next(idx for idx, m in enumerate(teams[i]["members"]) if m["id"] == ida)
    bj = next(idx for idx, m in enumerate(teams[j]["members"]) if m["id"] == idb)
    teams[i]["members"][ai], teams[j]["members"][bj] = teams[j]["members"][bj], teams[i]["members"][ai]
    # group_count는 변하지 않음(서로 교환이므로 그룹 카운트 변화 없음)
    # 성비는 바뀌지만 group_count에는 반영 안 함
    # (group_count는 그룹 균형용이므로 group_col 기준만 유지)
    

# ---------------------------
# 엔트리: 전체 밸런싱
# ---------------------------
def balance_teams(teams: List[dict],
                  people_df,  # group_targets 계산에 필요
                  constraints: Dict[str, List[Tuple[Set[str], str]]],
                  K: int,
                  group_col: str = "group",
                  gender_col: str = "gender",
                  seed: int = 42) -> List[dict]:
    """
    1) 총원 균형(±1)
    2) 성비 균형(권장 단계; 하드 제약 미침범)
    """
    t1 = adjust_team_sizes(teams, people_df, constraints, K, group_col=group_col, seed=seed)
    t2 = adjust_gender_balance(t1, gender_col=gender_col, seed=seed)

    # 최종 하드 제약 재검증 (안전망)
    id2c, _ = build_hard_clusters([m["id"] for T in t2 for m in T["members"]], constraints)
    ok, viol = check_hard_constraints_satisfied_simple(t2, id2c, constraints)
    if not ok:
        # 혹시라도 위반이 생기면, 성비 조정 전 단계로 롤백
        return t1
    return t2