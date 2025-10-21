import os
import random
from collections import Counter

import pandas as pd

# --- core modules ---
from constraints import (
    process_constraints_friendly,   # 사람친화(ref1/ref2) 제약 → 표준 제약
    build_hard_clusters,
    validate_hard_contradictions_simple,
)
from allocation import allocate_teams
from balance import (
    search_best_feasible,           # 여러 시드에서 최선 해 탐색(하드+하한+상한 보장)
)

from report import (
    export_team_matrix_excel,
    export_team_matrix_csv,
)

# ---------------------------
# 출력 유틸
# ---------------------------

def team_sizes(teams):
    return [len(t["members"]) for t in teams]

def team_gender_counts(teams, gender_col="gender"):
    return [Counter([m.get(gender_col) for m in t["members"]]) for t in teams]

def show_sizes_and_gender(teams, title="", gender_col="gender"):
    print(f"\n=== {title} ===")
    sizes = team_sizes(teams)
    if not sizes:
        print("팀이 비어있습니다.")
        return
    print("Team sizes:", sizes, f"(min={min(sizes)}, max={max(sizes)})")
    gc = team_gender_counts(teams, gender_col)
    for t, c in zip(teams, gc):
        print(f"Team {t['team_id']}: M={c.get('M',0)}, F={c.get('F',0)}")

def show_group_summary(teams, group_col="group"):
    print("\n=== GROUP SUMMARY ===")
    for t in teams:
        gid = t["team_id"]
        gmap = dict(t.get("group_count", {}))
        # 키 정렬해서 보기 좋게
        parts = ", ".join([f"{g}={gmap[g]}" for g in sorted(gmap.keys())])
        print(f"Team {gid}: {parts}")

def show_members(teams, title="MEMBERS", group_col="group", gender_col="gender"):
    print(f"\n=== {title} ===")
    for t in teams:
        print(f"\nTeam {t['team_id']} (size={len(t['members'])})")
        for m in sorted(t["members"], key=lambda x: x["id"]):
            print(f"  - {m['id']} {m.get('name','')} ({m.get(group_col,'?')}, {m.get(gender_col,'?')})")


# ---------------------------
# 메인 실행
# ---------------------------

if __name__ == "__main__":
    # ===== 설정 =====
    K = 8                          # 만들 팀 개수
    trials = 120                   # 탐색 시도 횟수(늘릴수록 품질↑, 시간↑)
    base_seed = 42                 # 재현용 시드(탐색 내부에서 개별 seed를 뽑음)
    group_col = "group"
    gender_col = "gender"

    # ===== 데이터 로드 =====
    # id 앞 ‘0’ 보존을 위해 dtype 지정
    people_df = pd.read_csv("data/people.csv", dtype={"id": str}, keep_default_na=False)
    # 사람친화 제약(컬럼: type, ref1, ref2, strength, notes(옵션))
    # ref는 ID 또는 이름(+힌트) 허용
    cons = process_constraints_friendly("data/constraints.csv", people_df)

    # ===== 하드 제약 내재 모순 체크(선행 검사) =====
    id2c, _ = build_hard_clusters(people_df["id"].tolist(), cons)
    contradictions = validate_hard_contradictions_simple(cons, id2c)
    print("Hard contradictions:", contradictions)  # []면 정상
    if contradictions:
        print("※ 하드 제약 자체가 충돌합니다. 제약 파일을 먼저 정리하세요.")
        # 계속 진행할 수도 있지만, 보통 여기서 종료하는 걸 권장
        # exit(1)

    # ===== (선택) 베이스라인: 단일 시드 1차 배정 미리 보기 =====
    baseline_seed = random.randint(1, 10**9)
    teams0 = allocate_teams(
        people_df,
        K=K,
        constraints=cons,
        seed=baseline_seed,
        group_col=group_col,
        gender_col=gender_col,
    )
    show_sizes_and_gender(teams0, title=f"BASELINE allocation (seed={baseline_seed})", gender_col=gender_col)
    show_group_summary(teams0, group_col=group_col)

    # ===== 다중 시드 탐색: 하드+하한+상한 보장 해 중 최선 선택 =====
    best_teams, info = search_best_feasible(
        people_df=people_df,
        constraints=cons,
        K=K,
        trials=trials,
        base_seed=base_seed,
        group_col=group_col,
        gender_col=gender_col,
    )

    print("\n=== SEARCH SUMMARY ===")
    print("Best seed:", info.get("best_seed"))
    print("Best score:", info.get("best_score"))
    print("Best size std:", info.get("best_size_std"))
    print("Best gender err:", info.get("best_gender_err"))
    print("Pareto top3:", info.get("pareto_top3"))

    if best_teams is None:
        print("\n❌ 탐색에서 유효한 해를 찾지 못했습니다.")
        print(" - 제약을 완화하거나(Gg<K인 학년은 최소 1명 조건 불가),")
        print(" - trials(시도 횟수)를 늘리거나,")
        print(" - people/constraints 데이터를 다시 확인하세요.")
        exit(1)

    # ===== 결과 출력 =====
    show_sizes_and_gender(best_teams, title="AFTER search (BEST FEASIBLE)", gender_col=gender_col)
    show_group_summary(best_teams, group_col=group_col)
    # show_members(best_teams, title="MEMBERS AFTER search", group_col=group_col, gender_col=gender_col)  # 필요시 주석 해제

    # ===== 리포트 저장 =====
    os.makedirs("output", exist_ok=True)
    export_team_matrix_excel(best_teams, filename="output/team_matrix.xlsx", show_legend=True)
    export_team_matrix_csv(best_teams, filename="output/team_matrix.csv")

    print("\n✅ Done. Reports saved under 'output/'.")