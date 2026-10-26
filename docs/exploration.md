# Render exploration

`game-auto exploration <run_id> --format json` reads the task's discovered page and element catalog
without starting a model, writer or device. Pages form a directed graph, including self loops and
shared destinations. Elements retain separate container/item identities; coordinates are evidence,
not permanent identifiers. Canvas candidates come from the planner's current sanitized image.

The TypeScript kernel alone commits catalog records and their correlated delta events in one
SQLite transaction. Records are scoped by task, APK workspace, credential profile and observed
identity. Cleanup removes catalog records with their owning task. Checkpoints retain action links,
not copies of the complete graph. Old executions without a catalog return an empty projection.

Locators refer to retained observations instead of copying full images or Skills. Discovery creates
a candidate locator; only post-action verification promotes it. A deviated result invalidates that
locator while retaining its history. Dynamic appearance does not establish a new business state.

Rendered screenshots do not prove delivery to a translation hook or whole-game text coverage.
