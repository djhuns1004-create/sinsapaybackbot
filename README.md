# 신사 주간 페이백봇 프리미엄 이모지 수정형

## 기존 기능 유지

- 페이백 신청
- 사진 첨부
- 성명/계좌 입력
- 관리자 승인
- 관리자 반려
- 지급완료
- SQLite 저장

## 관리자 문구 수정

텔레그램 관리자 계정에서:

```text
/admin
```

수정 가능:

- 제목
- 신청기간
- 지급조건
- 신청방법
- 신청버튼 문구
- 사진요청 문구
- 계좌요청 문구
- 신청완료 문구
- 전체 안내문 미리보기

## 프리미엄 이모지

관리자가 수정 문구를 보낼 때 프리미엄 이모지를 섞어 보내면
custom emoji ID를 저장하고 실제 안내문에 표시합니다.

텔레그램 API 제한상 인라인 버튼 글자에는 프리미엄 이모지
엔티티가 적용되지 않습니다. 메시지 본문과 안내문에는 적용됩니다.

## Railway Variables

```env
BOT_TOKEN=새 봇 토큰
OWNER_ID=122190868
DB_PATH=/data/payback.db
```

Volume Mount Path:

```text
/data
```

Start Command:

```text
python bot.py
```
