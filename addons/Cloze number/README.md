# Genuine Cloze Number in Browser + Column (v6)

- 検索欄右に **Genuine Cloze #: N**（非Clozeは `-`）
- 列 **"Cloze # (genuine)"** を追加（Cards=番号、Notes=一覧）
- v6: 行データ埋め込みの互換性をさらに強化（3〜5引数の全部に対応、
  active columns が取れない場合は**最後のセルに書くフォールバック**付き）

## 使い方
1. ZIP をインストール
2. ブラウザ列ヘッダ右クリック → **列の表示** → **Cloze # (genuine)** をオン  
   （列を右端に置くとフォールバックとも相性が良いです）

## ソート
このビルドは Column に `sorting` が無いため、ヘッダクリックでの並べ替えは不可です。  
代わりに **Card 列**で並び替えると、*genuine cloze (= card.ord + 1)* に準じた並び替えになります。

© 2025-10-16
