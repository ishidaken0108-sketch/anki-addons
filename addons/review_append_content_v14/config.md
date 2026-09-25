# Review Append Content 設定

- `targetTag`: 追加先ノートにつけるタグ
- `sourceContentField`: 復習中カードからコピーするフィールド
- `targetContentField`: 箇条書きを持つ追加先フィールド
- `mnemonicsField`: Mnemonics フィールド名
- `buttonLabel`: Review画面のボタン名
- `autoAddNewAgain`: 新規カードで「もう一度」のとき自動追加するか

## Mnemonics の Cloze 化

- `:` または `：` が1つも無い場合: Mnemonics 全体を `{{c1::...}}` にします。
- `:` または `：` がある場合: 各行ごとに、最初のコロンより後ろだけを `{{c1::...}}` にします。
- Mnemonics が空欄なら Mnemonics 部分自体を作りません。
