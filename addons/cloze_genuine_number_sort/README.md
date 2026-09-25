# Browse Sort by Genuine Cloze Number

Anki の Browse 画面に `Genuine Cloze #` 列を追加し、
`c1, c2, c3...` の genuine cloze 番号でソートできるようにするアドオンです。

## 対応想定
- Anki 2.1.45 以降
- 特に最近の Anki 25 系を想定

## 使い方
1. アドオンを `addons21` 配下に配置します。
2. Anki を再起動します。
3. Browse 画面で列の表示設定から `Genuine Cloze #` を有効化します。
4. 列ヘッダをクリックすると昇順、再クリックで降順に並べ替えできます。

## 挙動
- Cloze ノートでは genuine cloze 番号を表示します。
- Notes モードでは、そのノートに含まれる genuine cloze 番号を `1, 2, 3` のように表示します。
- 非 cloze ノート/カードは空欄になり、並べ替え時は後ろに回します。
