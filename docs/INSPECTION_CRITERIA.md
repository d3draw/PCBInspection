# 검사 기준서 (Inspection Criteria)

> **이 문서의 위치**: PLAN.md §3 핵심 설계 원칙 1 ("검사 기준서를 먼저 확보하라"),
> §4 (검사 기준서 필수 선행 작업)의 실체.
> 이 문서가 합의·승인되기 전까지 라벨링·모델 학습은 시작하지 않는다.

| 항목 | 값 |
|------|-----|
| 버전 | 0.1 (초안 / draft) |
| 상태 | ⚠ **미승인 — QA·생산·라인운영 합의 대기** |
| 최종 수정 | 2026-05-04 |
| 승인자 | (미정) |
| 다음 리뷰 | (미정) |

---

## 0. 합의 체크리스트 (사전)

라벨링 시작 전 아래 모든 항목이 ✅로 체크되어야 한다.

- [ ] 결함 taxonomy (§2) 합의
- [ ] 항목별 수치 기준 (§3) 모든 ⚠ TBD 채움
- [ ] 경계 이미지 (§4) 각 클래스 OK/NG 각 ≥ 5장 등록
- [ ] 우선순위 (§5) 합의
- [ ] 라벨러 교육 자료 (§6) 작성
- [ ] QA 서명 (§8)

---

## 1. 적용 범위

| 항목 | 정의 |
|------|------|
| 대상 제품군 | ⚠ TBD (모델명/리비전 단위) |
| 대상 공정 위치 | SMT 후공정, 리플로우 후 |
| 검사 면 | Top side (Bottom은 차기 검토) |
| 비검사 항목 | BGA 하부 솔더, 내부 void, 솔더 높이 정량 (PLAN §2.2 참조 — X-ray/3D 필요) |

---

## 2. 결함 Taxonomy

PLAN §2.1과 1:1 대응. 클래스 ID는 라벨링·모델 출력에서 그대로 사용.

| ID | 카테고리 | 서브타입 | 라벨 키 | 검사 접근법 (PLAN §7) | Phase |
|----|----------|----------|---------|----------------------|:-----:|
| C1 | 부품 유무·오삽 | Missing | `missing` | Reference Comparison | 0 |
| C1 | 부품 유무·오삽 | Wrong component | `wrong_component` | Reference Comparison | 0 |
| C2 | 부품 극성 | Polarity error | `polarity` | Reference Comparison | 0 |
| C3 | 위치·회전 | Offset (xy 편차) | `offset` | Template + 기하 규칙 | 0 |
| C3 | 위치·회전 | Rotation | `rotation` | Template + 기하 규칙 | 0 |
| C4 | 납땜 품질 | Cold solder | `cold_solder` | Anomaly Detection | 0→1 |
| C4 | 납땜 품질 | Insufficient solder | `insufficient_solder` | Anomaly Detection | 0→1 |
| C4 | 납땜 품질 | Excess solder | `excess_solder` | Anomaly Detection | 0→1 |
| C4 | 납땜 품질 | Bridge | `bridge` | Anomaly Detection | 0→1 |
| C5 | 납볼·이물 | Solder ball | `solder_ball` | Blob / Segmentation | 0 |
| C5 | 납볼·이물 | Foreign material | `foreign_material` | Blob / Segmentation | 0→2 |
| C6 | 부품 들림 | Tombstone | `tombstone` | Anomaly + Rule (그림자) | 0 |
| OK | 정상 | — | `ok` | (negative) | 0 |
| BL | 경계 사례 | (모든 카테고리) | `borderline` | 별도 큐 — §4 참조 | 0 |

**라벨링 형식**:
- Phase 0 (anomaly): `ok` vs `not_ok` 이진 (anomalib good/defect 디렉토리 구조)
- Phase 1+ (분류): 위 라벨 키 1개 + (선택) 위치/크기 어노테이션

---

## 3. 항목별 수치 기준

> 각 항목은 **OK 상한**과 **NG 하한** 사이의 회색지대를 경계 사례(§4)로 분리한다.
> 빈 칸 (`⚠ TBD`)은 실 보드 측정 후 QA가 결정. 단위는 mm 또는 픽셀 명시 필수.

### 3.1 C1 Missing / Wrong (부품 유무·오삽)

| 항목 | OK 기준 | NG 기준 | 근거/메모 |
|------|---------|---------|-----------|
| 부품 존재 | 지정 좌표 ROI 내 부품 외형 검출 | 부품 미검출 | binary |
| 부품 종류 | CAD 매핑된 PN과 외형 일치 | 다른 부품 검출 | OCR 가능 시 마킹 일치 추가 검증 |
| 회전 (180° 오삽) | (C2 polarity와 별도 — 비대칭 부품에만 해당) | ⚠ TBD | 카테고리 명확화 필요 |

### 3.2 C2 Polarity (극성)

| 항목 | OK 기준 | NG 기준 | 근거/메모 |
|------|---------|---------|-----------|
| 극성 마킹 위치 | CAD 기준 방향과 일치 | 180°/90° 회전 | 다이오드/IC/전해콘 등 적용 |
| 적용 부품 목록 | (CAD designator 리스트로 관리) | — | ⚠ TBD - 부품 라이브러리 마킹 |

### 3.3 C3 Offset / Rotation (위치·회전)

| 항목 | OK 기준 | NG 기준 | 단위 | 근거 |
|------|---------|---------|:----:|------|
| Offset (소형 부품 0402/0603) | ≤ ⚠ TBD | > ⚠ TBD | mm | IPC-A-610 class 2 참고 |
| Offset (대형 부품 1206 이상) | ≤ ⚠ TBD | > ⚠ TBD | mm | |
| Rotation | ≤ ⚠ TBD | > ⚠ TBD | deg | 패드 노출 한계 기준 |
| 패드 노출 비율 | ⚠ TBD | ⚠ TBD | % | side overhang |

### 3.4 C4 납땜 품질

| 서브타입 | OK 기준 (정성) | NG 기준 (정성) | 정량 지표 (있을 시) |
|----------|----------------|----------------|---------------------|
| Cold solder | 광택, fillet 형성 | 무광·결정 모양·fillet 미형성 | 표면 텍스처 anomaly score ≤ ⚠ TBD |
| Insufficient | fillet height ≥ 패드 1/4 | fillet 거의 없음 | ⚠ TBD |
| Excess | 인접 패드 미접촉 | 패드 외 영역 침범 | ⚠ TBD |
| Bridge | 패드 간 분리 | 인접 패드/리드 단락 | 단락 픽셀 폭 ≥ ⚠ TBD |

> 납땜 항목은 **저각 링 조명 (PLAN §5.1)** 이미지 기준. 다른 조명 이미지 사용 시 별도 기준 필요.

### 3.5 C5 납볼 / 이물

| 항목 | OK 기준 | NG 기준 | 근거 |
|------|---------|---------|------|
| Solder ball 크기 | ≤ ⚠ TBD μm 또는 패드 외부 | > ⚠ TBD μm | IPC 참고 |
| Solder ball 개수 | ≤ ⚠ TBD 개/보드 | > ⚠ TBD | |
| 이물 (먼지/머리카락) | 표면적 < ⚠ TBD μm² | 표면적 ≥ ⚠ TBD μm² 또는 도전성 의심 | 도전성 이물 = 무조건 NG |

### 3.6 C6 Tombstone (부품 들림)

| 항목 | OK 기준 | NG 기준 |
|------|---------|---------|
| 한쪽 패드 들림 | 부품 양 끝 솔더링 모두 형성 | 한쪽 끝 패드 미접촉, 부품이 수직에 가까움 |
| 그림자 길이 비율 | ⚠ TBD | ⚠ TBD |

---

## 4. 경계 사례 (Borderline) 처리

PLAN §4.1 "허용/비허용 경계 이미지", §4.2 "라벨링 기준과 100% 동일하게 유지".

### 4.1 경계 이미지 저장 구조

```
data/criteria/borderline/
├── <class_label>/
│   ├── accept/           # OK로 판정된 경계 사례 (라벨러 교육용 OK 측 끝점)
│   │   ├── 001.png
│   │   └── meta.json     # 부품/조명/판정 사유
│   └── reject/           # NG로 판정된 경계 사례 (NG 측 끝점)
│       ├── 001.png
│       └── meta.json
```

- 각 클래스당 OK/NG 각 **최소 5장** 등록 후에야 해당 클래스 라벨링 시작 가능.
- `meta.json` 필수 필드: `class`, `subtype`, `decision` (`accept|reject`), `decided_by`, `decided_at`, `reason`.

### 4.2 경계 사례 처리 룰

1. 라벨러가 단독 판단 불가 → 라벨링 도구에서 `borderline` 큐로 이동 (판정 보류).
2. 엔지니어 주 1회 검토 (PLAN §6.3) → QA에 상정.
3. QA 결정 → 결정 결과를 `data/criteria/borderline/<class>/<accept|reject>/`에 추가 + 본 문서 §3 수치 기준 갱신 (필요 시).
4. 모델 학습 데이터셋에는 **결정된 경계 사례만 포함**, `borderline` 미결 상태는 제외.

---

## 5. 검사 항목 우선순위

| Tier | 의미 | 항목 |
|:----:|------|------|
| P0 | 미검출 = 라인 정지 | C2 Polarity (역삽), C4 Bridge, C5 도전성 이물 |
| P1 | 미검출 = 출하 불가 | C1 Missing/Wrong, C4 Cold/Insufficient, C6 Tombstone |
| P2 | 미검출 = 보수 가능 | C3 Offset/Rotation, C4 Excess, C5 비도전성 이물 |
| P3 | 통계 관리 항목 | (해당 항목 미정 — 차기) |

> P0 항목의 **미검출률(escape rate)** 은 0%를 목표로 한다 (반대급부로 과검출 허용).
> P2 이하는 과검출 비용이 더 크므로 보수적 임계 설정.

---

## 6. 라벨러 교육 자료 (체크리스트)

라벨러 1인 교육 시 아래를 모두 다룬 후 mock-라벨링 통과 확인.

- [ ] 본 문서 §2 taxonomy 암기
- [ ] §3 수치 기준 (자주 쓰는 항목 발췌본)
- [ ] §4 경계 이미지 (클래스별 5장 OK / 5장 NG 시연)
- [ ] `borderline` 큐 사용법
- [ ] **혼동 가능 케이스**: ⚠ TBD (예: cold solder vs. insufficient, solder ball vs. foreign material)
- [ ] mock 라벨 30장 → 기준 라벨과 일치율 ≥ 95% 통과

---

## 7. 변경 관리

PLAN §4.2 "QA 승인 없이는 변경 금지", "변경 이력 관리 (버전 + 승인자 + 사유)".

### 7.1 변경 절차

1. 변경 제안 (issue 또는 PR) → 영향 받는 §·항목 명시
2. QA·라인 운영·엔지니어 합의 (오프라인/회의록)
3. 본 문서 patch + 버전 increment + §7.2 표 한 줄 추가
4. 동시에: 라벨링 도구 가이드, 모델 학습 파이프라인의 임계값 설정 갱신

> 본 문서 변경 후 **1주 이내** 모든 라벨러 재교육.

### 7.2 변경 이력

| 버전 | 일자 | 변경 요약 | 승인자 |
|------|------|-----------|--------|
| 0.1 | 2026-05-04 | 초안 (PLAN.md §2/§4/§6 기반 골격) | (미승인) |

---

## 8. 승인

| 역할 | 이름 | 서명 | 일자 |
|------|------|------|------|
| QA | | | |
| 라인 운영 | | | |
| 엔지니어링 | | | |

> 위 3인 서명 후 버전 1.0 으로 승격 → 라벨링 시작 가능.

---

## 부록 A. 관련 문서

| 문서 | 참조 항목 |
|------|-----------|
| docs/PLAN.md | §2 검사 범위, §4 본 문서의 상위, §5 조명 ↔ 결함 매핑, §6.3 라벨링 전략 |
| docs/HARDWARE_SPEC.md | §6 조명 (검사 기준 측정 시 광원 명시 필수) |
| (외부) IPC-A-610 | 일반 SMT 허용 기준 — 본 문서 §3 작성 시 1차 참조 |

## 부록 B. 본 문서를 코드에서 사용하는 위치 (작성 예정)

- `prepare_anomaly_dataset.py`: §2 라벨 키로 디렉토리 구분
- 라벨링 도구 (Label Studio config): §2 라벨 키 + §4 borderline 큐 정의
- 모델 임계값 설정: §3 수치 기준을 YAML로 export → recipe 파일에 주입
- 운영 UI (Streamlit): 과검출 피드백 시 §2 라벨 + §4 borderline 옵션 노출

> 위 연결은 본 문서 1.0 승급 후 구현 (현재 초안 단계에서는 미연결).
