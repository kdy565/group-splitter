# 🧮 group-splitter

**조(Team) 자동 배정 및 균형화 시스템**  
인원 CSV와 제약 CSV를 입력받아,  
> 하드 제약(`together/ apart:hard`)을 절대 위반하지 않으면서  
> 학년별 최소 1명 보장, 과도한 집중 방지(cap),  
> 팀 크기·성비 균형을 동시에 고려해 자동으로 조를 배정합니다.

---

## 📁 프로젝트 구조

```
group-splitter/
├── main.py                # 전체 실행 진입점
├── constraints.py         # 제약조건 파싱 및 검증
├── allocation.py          # 기본 조 배정 알고리즘
├── balance.py             # 하드+학년 하한/상한 보장 + 다중시드 탐색형 균형 알고리즘
├── report.py              # 결과 리포트 (Excel/CSV)
├── data/
│   ├── people.csv         # 인원 목록 (id, name, group, gender)
│   └── constraints.csv    # 제약 목록 (type, id1/id2, strength, notes)
└── output/
    ├── team_report.xlsx   # 요약 리포트
    └── team_matrix.xlsx   # 가로형 이름 매트릭스
```

---

## ⚙️ 실행 방법

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pandas openpyxl

python main.py
```

---

## 🧠 주요 알고리즘 요약

### **1️⃣ constraints.py**
- CSV를 읽어 `together` / `apart` 조건을 구조화
- `hard` 제약을 기반으로 연결 그래프(클러스터) 구성
- 모순 검증:
  - 같은 쌍에 `together:hard`와 `apart:hard`가 동시에 존재하면 오류
  - 같은 클러스터 내부에 `apart:hard`가 있으면 오류

---

### **2️⃣ allocation.py**
- 그룹별 목표 인원 분포 계산 (예: 4학년 10명 → 3/3/4)
- 하드 클러스터 단위로 배정
- 팀별 group 균형 및 제약 조건 고려하여 랜덤 시드 기반 분배

---

### **3️⃣ balance.py (v2 — Hard-First, Multi-Start)**

#### 🧩 개요
이전 버전의 **스왑 기반 성비 조정**을 완전히 제거하고,  
하드 제약과 학년 분산(최소 1명, 상한 cap)을 **절대 보장하는 해**만 생성 후  
그중 **가장 균형이 좋은 해**를 선택하는 구조입니다.

#### ⚖️ 우선순위
1. **항상 보장**
   - `together:hard` → 같은 팀  
   - `apart:hard` → 다른 팀  
   - 학년별 **최소 1명**(가능할 경우)  
   - 학년별 **상한 cap = ceil(그 학년 인원 / 팀 수)**

2. **후순위(Soft)**
   - 팀 크기 표준편차 최소화 (인원 균형)
   - 전체 여성 비율 대비 편차 최소화 (성비 균형)

#### ⚙️ 동작 구조
```
[allocate_teams] → A. 학년 최소 1명 채우기 → B. 학년 cap 분산 → (옵션) C. 팀 크기 가벼운 조정
```

- A/B는 절대 보장, C는 하한·상한을 깨지 않는 범위에서만 수행됩니다.  
- 성비는 평가용 지표로만 사용하며, 스왑 기반 조정은 하지 않습니다.

#### 🧠 다중 시드 탐색 (Multi-Start)
- 여러 seed를 사용해 수십~수백 개의 feasible 해 생성  
- 각 해를 평가(`_score_soft`)하고 **가장 좋은 해**를 선택

#### 📈 평가 함수 `_score_soft`
| 지표 | 의미 | 기본 가중치 |
|------|------|--------------|
| `size_std` | 팀 크기 표준편차 (작을수록 균형) | 1.0 |
| `gender_err` | 전체 성비 대비 팀별 편차합 | 0.3 |

> `score = 1.0 × size_std + 0.3 × gender_err`

- `trials` 값을 늘릴수록(예: 60 → 300) 결과 품질이 향상됩니다.
- 성비를 거의 무시하고 싶다면 `w_gender=0.1` 이하로 낮추면 됩니다.

#### 🧩 보장 조건
- 모든 팀은 **가능한 경우 모든 학년을 최소 1명 이상 포함**
- 특정 학년이 과도하게 몰리지 않음 (cap 보장)
- 모든 하드 제약 위반 불가 (together / apart:hard)

#### ✅ 장점
- 앞 단계에서 맞춘 균형을 절대 깨지 않음
- 복잡한 스왑 탐색 제거 → 단순하고 빠름
- 결과 예측 가능성 및 재현성 향상

---

## 📊 예시 결과 (team_matrix.xlsx)

| Team 1 | 김도연 | 이서현 | 박지훈 | 최예린 | ... |
|---------|---------|---------|---------|---------|  
(셀 색상은 각 group별로 구분됨, 예: 4학년은 노랑, 3학년은 하늘 등)

---

## 🧾 출력 요약
- **team_report.xlsx**: 팀별 요약 통계 (인원 수, 성비, 그룹 구성 등)
- **team_matrix.xlsx**: 각 팀을 한 행(row)에 표시, 그룹별 색상 배경 적용

---

## 🧩 특징 요약
- ✅ **하드 제약 절대 보장**  
- ✅ **학년 최소 인원 및 cap 분산 보장**  
- ✅ **다중 시드 탐색 기반 최적 균형 선택**  
- ✅ **성비·총원 균형은 평가로만 반영 (스왑 없음)**  
- ✅ **결과 시각화 리포트 자동 생성**  

---

## 👤 개발자 노트
- Python 3.9 이상 권장
- 주요 라이브러리:
  - `pandas` – CSV 입출력 및 데이터 구조화
  - `openpyxl` – Excel 시각화
  - `collections`, `random`, `typing` – 데이터 모델 및 구조화

---

## 🧾 License
MIT License  
Copyright © 2025
