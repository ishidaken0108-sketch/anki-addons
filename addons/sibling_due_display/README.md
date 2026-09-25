# 兄弟カード期日表示 (Sibling Due Display)

レビュー中の「兄弟カード（同じノートから作られた他のカード）」について、**次回表示タイミング（期日）だけ**を、**レビュー画面内**にバッジ形式で強調表示します。

## 表示例
- `1 2026-02-10`（復習カード：日付）
- `2 35m`（学習中：残り時間）
- `3 New`（新規）
- `4 停止` / `埋め`

## 対応バージョン
- Anki 24.06+ を想定（Qt5）

## 設定 (config.json)
- `enabled`: 表示する/しない
- `position`: `bottom` / `top`
- `show_on_question`, `show_on_answer`: 問題/解答どちらに表示するか
- `max_siblings`: 表示する兄弟カード数の上限
- `show_when_no_siblings`: 兄弟がいない時も表示枠を出す
- `debug`: true にすると、問題発生時にツールチップで通知

## 実装メモ
- `gui_hooks.card_will_show`（存在しない場合は reviewer 系 hook にフォールバック）で HTML に挿入しています。
- 期日計算は `mw.col.sched.today` を基準にした相対日数を `date.today()` に加算しています。


## 更新 (0.2.2)
- 期日の横に「何日後/何日前/今日」を表示
- 表示されない問題を修正（CSS内の波括弧のエスケープ）


## 0.2.3
- Fix: inject failed 'str is not callable' (removed accidental double-call in injection)


## 追加: 難しい/普通の次回期日予測
- 復習カード(Review)について、現在のinterval(ivl)とEase(factor)、Deck Options の hardFactor/ivlFct を使って
  「次の期日に Hard(難しい) / Good(普通) を押した場合の次回期日(概算)」を表示します。
- FSRS等の別アルゴリズムを使っている場合は実際と一致しない可能性があります。
