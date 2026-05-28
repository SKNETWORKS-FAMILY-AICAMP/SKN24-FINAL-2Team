# SK네트웍스 Family AI 캠프 24기 최종 프로젝트 중간 발표

# 1. 팀 소개
좌우지간 (김은우, 김현수, 나혜린, 박정은, 전윤우)

# 2. 프로젝트 개요

## 2-1. 프로젝트 명
POLICITY

## 2-2. 프로젝트 소개
> POLICITY는 청년층(대한민국 2030)의 정치 참여 장벽을 낮추기 위해 설계된 AI 기반 정치 입문 플랫폼입니다.
> 
> 서비스는 **찬반 입장을 선택하여 AI와 1대 1로 토론하는 기능**과,
>
> **AI 간 토론을 참관하는 AI 토론 기능**으로 구성됩니다.
>
> 대화 기능과 토론 기능을 위해, '온통청년'의 청년 정책 기준인 일자리, 교육, 주거, 금융, 생활복지, 문화에 해당하는 대분류 하의 법안, 정책, 뉴스 기사를 기반으로 **'정보 카드'**를 제공합니다.
>
> 정보 카드에는 **뉴스 카드**와 **정책 카드**가 있습니다.
>
> 뉴스 카드의 경우 특정 이슈에 대해 핵심 내용을 요약하고, 청년 일상과의 연관성을 여러 기사들을 통해 설명하고, 정책 카드의 경우 청년과 관련된 정책의 핵심 내용을 요약하고 일상과의 연관성을 설명합니다.
>
> 정책카드의 경우 지원 대상, 신청 및 마감 날짜, 혜택 의 내용을 확인할 수 있고, 해당 정책에 대한 찬성 및 반대 의견을 중립적으로 제시합니다.

## 2-3. 프로젝트 필요성
### 2-3-1. 청년 정치 참여 현황
> 민주주의는 시민의 참여로 작동하고, 대표자가 선출되어야 정책이 발의되고, 그 정책은 결국 시민의 삶과 직결됩니다. 현재 대한민국 정치에 대한 관심과 참여로 대표할 수 있는 투표율을 확인해보면, 2024년 4월 10일 총선 투표율을 기준으로 20대가 가장 낮고, 30대가 그 뒤를 잇습니다.

![image_1](./readme_images/img_1.png)

> 청년층의 투표율이 낮다는 것은 청년의 이익이 정책의 발의에 있어 소외되는 악순환으로 이어집니다. 청년이 투표장에 나타나지 않을 수록 국가의 중요한 의사결정에 청년의 목소리가 반영되는 비중이 적어지게 됩니다.

### 2-3-2. 진입 장벽의 문제
> 대학생 설문(KNSU 미디어, 2024)에서 투표가 중요성을 알고 있다고 응답한 비율은 84%에 달했지만, 실제 참여(투표)로 이어지는 비율은 65% 뿐입니다.
>
> GIST 학생 설문(n=182)에 따르면 필요성은 인식하고 있지만 실제 참여로 이어지지 않는 주요 이유는 두 가지가 있습니다.
> 
> 첫 번째, 정보 접근의 어려움입니다. **법안과 정책 용어가 어렵고, 언론사마다 해석이 엇갈리며, 정치 이슈가 내 삶과 어떻게 연결되는지 체감하기 어렵다**는 응답이 확인되었습니다.
> 
> 두 번째, 표현에 대한 부담입니다. 응답자의 70.9%가 SNS에 정치 의견을 올린 적 없고, 앞으로 올리지 않겠다고 밝혔습니다.
> 
> 이는 **다툼의 위험과 정치 성향 노출에 대한 두려움** 때문인 것입니다.
>
> 결국 청년 정치 참여의 문제는 관심의 부재가 아닌, 쉽게 접근하고 부담없이 이야기할 수 있는 창구가 없다는 구조적인 문제에 가깝습니다.

### 2-3-3. 토론과 학습의 필요성
> 정치 참여에서 중요한 것은 단순히 정보를 습득하는 것이 아닌, 다양한 입장을 직접 접하고 자신의 생각을 정리해보는 과정이 정치에 대한 이해를 깊게 만듭니다. Argyle et al.(2023, PNAS)에 따르면 자신의 입장을 말하고 반대 논거를 접하는 구조가 대화의 질을 높이고 다양한 관점에 대한 이해를 넓힐 수 있다고 하였습니다.

# 3. WBS
https://docs.google.com/spreadsheets/d/1lBx_-zMlK1x6_ZCEh6PZAWjJvdMrjnkb/edit?gid=354776593#gid=354776593

# 4. 🛠️ 기술 스택
| Category | Stack / Icons |
| :--- | :--- |
| **Frontend / Client** | ![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=for-the-badge&logo=html5&logoColor=white) ![CSS3](https://img.shields.io/badge/CSS3-1572B6?style=for-the-badge&logo=css3&logoColor=white) ![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black) |
| **Backend Core** | ![Django](https://img.shields.io/badge/Django-092E20?style=for-the-badge&logo=django&logoColor=white) ![Gunicorn](https://img.shields.io/badge/Gunicorn-499A4C?style=for-the-badge&logo=gunicorn&logoColor=white) ![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white) |
| **AI Agent Layer** | ![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi&logoColor=white) ![Uvicorn](https://img.shields.io/badge/Uvicorn-434343?style=for-the-badge&logo=uvicorn&logoColor=white) ![LangChain](https://img.shields.io/badge/LangChain-1C3C3A?style=for-the-badge&logo=langchain&logoColor=white) ![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3A?style=for-the-badge&logo=langchain&logoColor=white) |
| **AI / ML Models** | ![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white) ![Hugging Face](https://img.shields.io/badge/Hugging%20Face-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black) |
| **Data Pipeline <br>(Serverless)** | ![AWS EventBridge](https://img.shields.io/badge/AWS%20EventBridge-FF4F8B?style=for-the-badge&logo=amazoneventbridge&logoColor=white) ![AWS Lambda](https://img.shields.io/badge/AWS%20Lambda-FF9900?style=for-the-badge&logo=awslambda&logoColor=white) ![RunPod](https://img.shields.io/badge/RunPod-6E56CF?style=for-the-badge&logo=runpod&logoColor=white) |
| **Relational Database** |  ![MySQL](https://img.shields.io/badge/MySQL-4479A1?style=for-the-badge&logo=mysql&logoColor=white) ![AWS RDS](https://img.shields.io/badge/AWS%20RDS-527FFF?style=for-the-badge&logo=amazonrds&logoColor=white)  |
| **Vector Database** | ![Qdrant](https://img.shields.io/badge/Qdrant-FF4034?style=for-the-badge&logo=qdrant&logoColor=white) |
| **Infrastructure <br>& DevOps** | ![Nginx](https://img.shields.io/badge/Nginx-009639?style=for-the-badge&logo=nginx&logoColor=white) ![Amazon EC2](https://img.shields.io/badge/Amazon%20EC2-FF9900?style=for-the-badge&logo=amazonec2&logoColor=white) ![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white) |
| **Storage** | ![AWS S3](https://img.shields.io/badge/AWS%20S3-569A31?style=for-the-badge&logo=amazons3&logoColor=white) |

---

# 5. ERD
<img width="4140" height="2022" alt="final project" src="https://github.com/user-attachments/assets/5d30a00f-ba1e-4b85-a6ba-261f6dfca59f" />

# 6. 요구사항 명세서
https://docs.google.com/spreadsheets/d/1opU1mVYwNAtQ2xCDJ8987yVDSA7gmTgXsWlMCGEGv0U/edit?gid=1374958457#gid=1374958457

# 7. 시스템 아키텍처
<img width="2484" height="1394" alt="image" src="https://github.com/user-attachments/assets/1df54221-fd6e-4c58-a171-3d593a8295f8" />

## 7-1. 데이터 수집 및 파이프라인
> 매일 오전 2시 정기 스케줄러(EventBridge Scheduler)에서 웹 크롤링을 수행하고, 수집된 원천 데이터(Raw Data)는 전처리된 형태로 AWS S3에 저장됩니다.
> 
> Runpod 서버에서 임베딩 처리가 진행되는데, 원천 데이터를 전처리한 후 ko-sroberta-multitask 모델을 통해 벡터로 변환하여 임베딩한 값이 Qdrant에 저장되며, 구조화된 정보 카드 및 원천 데이터는 AWS의 RDS(MySQL)에 최종적으로 연동됩니다.

## 7-2. 백엔드 및 프론트엔드 서버
> Docker를 활용하여 백엔드와 AI에이전트 각각의 구동 환경을 컨테이너화하여 로컬 개발부터 상용 배포까지의 인프라 환경의 일관성을 유지힙니다. Nginx를 활용하여 외부 클라이언트의 요청을 받아 내부의 Gunicorn 및 Django로 라우팅하고 정적인 데이터를 처리합니다.

## 7-3. AI 에이전트 서버
> FastAPI를 활용하여 Django 백엔드 서버와 AI 에이전트 간의 통신을 중개하고, LangChain과 LangGraph에서 에이전트의 판단 흐름과 멀티 스텝 워크플로우를 구조적으로 제어합니다.

## 7-4. 활용 모델
> 에이전트의 최종 결과물 생성을 담당하는 메인 모델은 OpenAI의 GPT-4o-mini, 임베딩 모델은 Ko-sroberta-multitask를 사용하였습니다.

## 7-5. 데이터 베이스 및 스토리지
> 회원 계정 정보, 전처리된 데이터, 토론, 채팅, 카드 정보 등 정형화된 데이터 관리를 위한 관계형 데이터베이스인 AWS RDS, RAG 구현을 위한 벡터 데이터베이스인 Qdrant, 크롤링한 대용량 원천 데이터 파일을 업로드하는 객체 스토리지 공간인 AWS S3를 활용하였습니다.

# 8. 전처리 과정
## 8-1. 법안 데이터
| 목록 | 내용 |
|------|------|
| **데이터명** | 열린 국회 정보 OpenAPI 법안 데이터 |
| **수집 건수** | 초기 데이터: 17000건 / 청년 관련 필터링 후: 250건 |
| **출처 및 저작권** | 대한민국 국회 |
| **출처** | https://open.assembly.go.kr/portal/openapi/main.do |
| **저장 포맷 / 인코딩** | pdf |

> 원천 데이터의 파일 형식이 pdf이기 때문에 LLM이 읽기 쉬운 형태인 마크다운 형태로 변환하였고, pdf 중 대부분은 현행 법안과 개정 법안을 대조하는 표가 있어 해당 부분을 처리하였습니다.

## 8-2. 뉴스 기사 데이터
| 목록 | 내용 |
|------|------|
| **데이터명** | 네이버 뉴스 기사 |
| **수집 건수** | 평균 데이터 900건 |
| **출처 및 저작권** | 네이버 뉴스 |
| **출처** | https://news.naver.com/ |
| **저장 포맷 / 인코딩** | txt, jsonl / utf-8 |

> 뉴스 기사를 크롤링하는 과정에서 광고 문구, 뉴스 기사 내용 자체가 아닌 다른 뉴스 기사 추천 리스트, 불필요한 특수문자, SNS 홍보문구, 이메일 등 여러 노이즈가 많이 있었기 때문에 이를 처리하였습니다.
> 
> 단순 정규표현식만으로 정제를 하기에는 그 양상이 다양했고, 뉴스 기사처럼 종결어미가 ‘-다’로 끝나더라도 실제 내용은 뉴스 기사가 아닌 경우도 있었기 때문에 이를 처리 하기 위해서는 단순 정규표현식만으로는 제거하기 어려워서 Kiwi를 활용하여 품사를 구분한 뒤에 추가적인 노이즈를 제거하는 방법을 택했습니다.

## 8-3. 정책 데이터
| 목록 | 내용 |
|------|------|
| **데이터명** | 행정안전부_대한민국 공공서비스(혜택) 정보 |
| **수집 건수** | 초기 데이터 10954건 / 청년 관련 필터링 후 1690건 |
| **출처 및 저작권** | 국무조정실 청년정책조정실 |
| **출처** | https://www.youthcenter.go.kr/main |
| **저장 포맷 / 인코딩** | json |

> 원본 json 파일에서 조회수와 같은 불필요한 필드를 제거하였고, 선정 기준, 지원 내용, 지원 대상 등에서는 특수문자가 텍스트 내용에 직접적으로 미치는 경우를 제외한 나머지 특수문자를 제거하였다.

# 9. 모델 선정 과정
> BAAI/bge-m3 vs jhgan/ko-sroberta-multitask

| 항목 | bge-m3 | ko-sroberta ⭐ 추천 모델 |
|------|--------|------------------------|
| **벡터** | Dense 1024-dim + Sparse (lexical weight) | Dense 768-dim only (Sparse 없음) |
| **언어 지원** | 다국어 지원 / 최대 8,192 토큰 처리 | 한국어 특화 / SRoBERTa · KLUE 사전학습 |
| **검색 방식** | 하이브리드 검색 — RRF Fusion (Dense + Sparse) | Dense 검색 — Cosine 유사도 |
| **임베딩 속도** | ~20분 / 153건 (GPU 권장) | ~2분 / 153건 (경량, 빠름) |

  
# 10. 진행 사항 및 개선 사항
## 10-1. 데이터 수집
> 정책 데이터의 경우 현재 행안부에서 전체 정책을 수집한 뒤 GPT-4o-mini를 활용해 청년 관련 정책만을 필터링하였고, 뉴스 기사의 경우 대분류별 Top5의 기사를 선정한 뒤 해당 정책명으로 네이버 뉴스를 수집하였습니다. 법안의 경우 열린 국회 정보 OpenAPI를 활용하여 법안 pdf를 수집한 후 GPT-4o-mini로 청년 관련 법안만 필터링하여 대분류에 따라 구분하였습니다. 추후에는 현재 시행 중인 정책의 근거가 되는 법령까지 수집할 예정입니다.

## 10-2. 데이터 전처리
> 현재 전처리의 전체적인 파이프라인은 구축되었지만 오전 02:00 자동화 파이프라인은 아직 완성되지 않았기 때문에 앞으로는 이 과정을 수행할 예정입니다. RAG 성능 고도화 및 Hallucination 제거를 위해 전처리 전략을 고도화 할 예정입니다.

## 10-3. 정보 카드
> clean text 전체를 주는 방식으로 기본 틀을 제작하고, 탭별로 노드를 세분화하고 현재 정책 카드 고도화에 집중하고 있습니다. 다양한 의견 생성 노드를 찬성, 반대 노드로 분리하고 있습니다. 핵심 내용 생성 노드, 반대 의견 생성 노드에 supervisor를 부여해서 검색 툴을 사용하게 해서 내용 생성, 편향 검사, 재생성 여부를 판단하도록 변경하였습니다. 데모 제작을 위해 정책 카드 변경 내용을 뉴스카드에도 반영하였습니다.

## 10-4. 채팅
> 카드 추천, 사용자가 카드를 선택하고 쿼리를 입력했을 때 해당 카드와 관련이 있는지, 혹은 새로운 카드를 추천해야하는지, 추천을 한다면 어떤 카드를 추천해야 하는지를 멀티 에이전트로 변환하였습니다.

## 10-5. 토론

# 11. 기대 효과
## 11-1. 사용자 관점
> 어려운 정책 용어와 복잡한 이슈를 일상 언어로 풀어줌으로써 정치 정보 접근의 장벽을 낮출 수 있으며, AI와의 1대1 토론을 통해 다툼의 부담 없이 다양한 관점을 접하고 자신의 생각을 정리해볼 수 있습니다.

## 11-2. 사회적 관점
> 정치 참여 의향은 있으나 진입 장벽으로 인해 행동하지 못했던 청년층의 실질적 참여를 이끌어 민주주의 대표성 향상에 기여할 수 있습니다.
> 
> 편향 방지 설계를 통해 특정 정당·후보에 치우치지 않는 균형 잡힌 정치 정보 소비 환경을 조성할 수 있습니다.
>
> AI 기반 토론 학습 환경 제공으로 청년의 정치적 이해도 향상에 기여할 수 있습니다.


