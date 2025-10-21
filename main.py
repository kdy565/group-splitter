import pandas as pd
from collections import Counter

# --- import core modules ---
from constraints import (
    process_constraints_friendly,
    build_hard_clusters,
    validate_hard_contradictions_simple,
)
from allocation import allocate_teams
from balance import balance_teams
from report import export_team_matrix_excel, export_team_matrix_csv
import random

# ---------------------------
# 출력 유틸 함수들
# ---------------------------

def team_sizes(teams):
    return [len(t["members"]) for t in teams]

def team_gender_counts(teams, gender_col="gender"):
    return [Counter([m.get(gender_col) for m in t["members"]]) for t in teams]

def show_sizes_and_gender(teams, title="", gender_col="gender"):
    print(f"\n=== {title} ===")
    sizes = team_sizes(teams)
    print("Team sizes:", sizes, f"(min={min(sizes)}, max={max(sizes)})")
    gc = team_gender_counts(teams, gender_col)
    for t, c in zip(teams, gc):
        print(f"Team {t['team_id']}: M={c.get('M',0)}, F={c.get('F',0)}")

def show_group_summary(teams, group_col="group"):
    print("\n=== GROUP SUMMARY ===")
    for t in teams:
        gid = t["team_id"]
        gmap = dict(t["group_count"])
        parts = ", ".join([f"{g}={gmap[g]}" for g in sorted(gmap.keys())])
        print(f"Team {gid}: {parts}")


# ---------------------------
# 메인 실행
# ---------------------------

if __name__ == "__main__":
    K = 8          # 만들 팀 개수
    seed = random.randint(0, 1000000)  # 실행마다 바뀌는 랜덤 시드

    # 데이터 로드
    people_df = pd.read_csv("data/people.csv", dtype={"id":str})
    cons = process_constraints_friendly("data/constraints.csv",people_df)

    # 하드 제약 모순 체크
    id2c, c2ids = build_hard_clusters(people_df["id"].tolist(), cons)
    contradictions = validate_hard_contradictions_simple(cons, id2c)
    print("Hard contradictions:", contradictions)  # []면 정상

    # -------------------
    # 1️⃣ 1차 배정 (allocation)
    # -------------------
    teams0 = allocate_teams(
        people_df,
        K=K,
        constraints=cons,
        seed=seed,
        group_col="group",
        gender_col="gender",
    )

    show_sizes_and_gender(teams0, title="BEFORE balance (allocation result)", gender_col="gender")
    show_group_summary(teams0, group_col="group")

    # -------------------
    # 2️⃣ 밸런싱 단계 (balance)
    # -------------------
    teams1 = balance_teams(
        teams0,
        people_df,
        cons,
        K=K,
        group_col="group",
        gender_col="gender",
        seed=seed,
    )

    show_sizes_and_gender(teams1, title="AFTER balance", gender_col="gender")
    show_group_summary(teams1, group_col="group")

    # -------------------
    # 3️⃣ 상세 결과 (멤버 목록)
    # -------------------
    print("\n=== MEMBERS AFTER balance ===")
    for t in teams1:
        print(f"\nTeam {t['team_id']} (size={len(t['members'])})")
        for m in sorted(t["members"], key=lambda x: x["id"]):
            print(f"  - {m['id']} {m.get('name','')} ({m.get('group','?')}, {m.get('gender','?')})")

    export_team_matrix_excel(teams1, filename="output/team_matrix.xlsx", show_legend=True)
    export_team_matrix_csv(teams1, filename="output/team_matrix.csv")