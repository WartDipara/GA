import { historySearch } from '@game-automation/contracts/workspace';
import { HistoryIndex } from './history-index.js';

const index = new HistoryIndex();
let revision = -1;
addEventListener('message', (event: MessageEvent<unknown>) => {
  const message = historySearch(event.data);
  if (message?.type === 'index') {
    revision = message.revision;
    index.replace(message);
  } else if (message?.type === 'query' && message.revision === revision) {
    void index
      .search(message, () => message.revision !== revision)
      .then((result) => {
        if (result) postMessage(result);
      })
      .catch(() => {
        // The owner retains the visible snapshot and falls back after its deadline.
      });
  }
});
