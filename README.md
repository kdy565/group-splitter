# 🧮 group-splitter

**조(Team) 자동 배정 및 균형화 시스템**  
인원 CSV와 제약 CSV를 입력받아,  
> 하드 제약(`together/ apart:hard`)을 절대 위반하지 않으면서  
> 학년·성비·팀 크기 균형을 고려해 자동으로 조를 배정합니다.

---

## 📁 프로젝트 구조

```
group-splitter/
├── main.py                # 전체 실행 진입점
├── constraints.py         # 제약조건 파싱 및 검증
├── allocation.py          # 기본 조 배정 알고리즘
├── balance.py             # 팀 크기/성비 균형 알고리즘
├── report.py              # 결과 리포트 (Excel/CSV)
├── data/
│   ├── people.csv         # 인원 목록 (id, name, group, gender)
│   └── constraints.csv    # 제약 목록 (type, id1, id2, strength, notes)
└── output/
    ├── team_report.xlsx   # 요약 리포트
    └── team_matrix.xlsx   # 가로형 이름 매트릭스
```

---

## ⚙️ 설치 및 실행

### 1️⃣ 환경 세팅

```bash
python -m venv .venv
source .venv/bin/activate

pip install -U pandas openpyxl
```

### 2️⃣ 입력 데이터 준비

**예시 – `data/people.csv`**

| id | name | group | gender |
|----|------|--------|--------|
| P001 | 김도연 | 4학년 | M |
| P002 | 이서현 | 4학년 | F |
| P003 | 박지훈 | 4학년 | M |
| P004 | 최예린 | 4학년 | F |
| ... | ... | ... | ... |

**예시 – `data/constraints.csv`**

| type | id1 | id2 | strength | notes |
|------|-----|-----|-----------|--------|
| together | P001 | P002 | hard | 4학년 조교 둘은 반드시 같은 조 |
| together | P001 | P003 | hard | 연애중 |
| together | P003 | P004 | hard | 성향이 안 맞음 |
| together | P009 | P010 | soft | 같은 실험조라 함께 있으면 좋음 |
| apart | P011 | P012 | soft | 비슷한 역할 피하기 |

---

## 📊 예시 스크린샷

아래는 `team_matrix.xlsx` 결과 예시입니다 (가상의 데이터 기반):

![예시 스크린샷](https://i.imgur.com/7fX6C2B.png)

각 팀이 한 줄(row)에 표시되며, 그룹별 배경색으로 구분됩니다.

---

## 🧠 주요 알고리즘

### **1️⃣ constraints.py**
- CSV를 읽어 `together` / `apart` 조건을 구조화
- `hard` 제약을 기반으로 연결 그래프(클러스터) 구성
- 모순 검증:
  - `together:hard` + `apart:hard` 동시 존재 시 오류
  - 같은 클러스터 내에 `apart:hard` 존재 시 오류

### **2️⃣ allocation.py**
- 그룹별 목표 인원 분포 계산 (예: 4학년 10명 → 3/3/4)
- 하드 클러스터 단위로 배정
- 팀별 group 균형 및 제약 조건 고려하여 랜덤 시드 기반 분배

### **3️⃣ balance.py**
- **팀 크기 조정:** 모든 팀이 ±1명 이내로 균형
- **성비 조정:** 전체 성비와 팀별 성비 편차 최소화
- 하드 제약 위반 시 스왑 무효 처리

### **4️⃣ report.py**
- 결과를 **Excel/CSV**로 내보냄
- `team_matrix.xlsx`에서는
  - 각 팀이 한 행
  - `group` 오름차순 → `이름` 가나다순 정렬
  - 그룹별 색상으로 배경 칠함
  - Legend 시트에서 색상표 확인 가능

---

## 🧾 출력 예시 (team_matrix.xlsx)

| Team 1 | 김도연 | 이서현 | 박지훈 | 최예린 | ... |
|---------|---------|---------|---------|---------|  
(셀 색상은 각 group별로 구분됨, 예: 4학년은 노랑, 3학년은 하늘 등)

---

## 🧩 특징
- ✅ **제약 기반 배정:** 함께/분리 조건을 자동 처리  
- ✅ **하드·소프트 구분:** 반드시/가급적 구분 가능  
- ✅ **성비·인원 균형 자동화:** 전체 평균에 맞춰 스왑 기반 조정  
- ✅ **색상 기반 시각화 리포트:** 팀별 구성 시각적으로 확인 가능  
- ✅ **재현성 보장:** `seed`로 랜덤 배정 재현 가능  

---

## 👤 개발자 노트
- Python 3.9 이상 권장
- 주요 라이브러리:  
  - `pandas` – CSV 입출력 및 데이터 구조화  
  - `openpyxl` – Excel 색상 및 시각화 리포트  
  - `collections`, `random`, `typing` – 데이터 모델  

---

## 🧾 License
MIT License  
Copyright © 2025
