# New Cards Created Order 設定

- `auto_reorder_on_profile_open`: プロファイルを開いた時に自動で並べ直します。
- `auto_reorder_after_sync`: 同期後に自動で並べ直します。
- `auto_reorder_when_deck_browser_is_shown`: デッキ一覧が表示された時に自動で並べ直します。
- `show_auto_tooltip`: 自動並べ直しで変更があった時に通知を表示します。
- `oldest_first`: `true` なら古い作成日のカードから導入します。`false` なら新しい作成日のカードから導入します。
- `starting_due_number`: 新規カード位置の開始番号です。通常は `1` のままで問題ありません。
- `include_suspended_and_buried_new_cards`: 停止中・埋められた新規カードも含めて並べ直します。

このアドオンは新規カードの `due` 位置を、ノート作成日時、カードテンプレート番号、カードIDの順で振り直します。
Ankiのデッキオプションで新規カードの収集順や表示順をランダムにしている場合、Anki側の設定が優先されます。
