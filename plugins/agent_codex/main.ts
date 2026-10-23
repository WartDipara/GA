import { metadata } from '../../var/cache/typescript/generated/agent-metadata.js';
import { object } from '../../src/control_plane/contracts.js';
import { ErrorCode, FrameworkError, frameworkError } from '../../src/control_plane/errors.js';
import { Peer } from '../../src/control_plane/protocol/peer.js';
import { CodexAdapter } from './adapter.js';

let invoking = false;
const peer: Peer = new Peer(
  process.stdin,
  process.stdout,
  async (method, params): Promise<unknown> => {
    if (method === 'initialize') {
      if (params['protocol_version'] !== metadata.protocol_version)
        throw new FrameworkError(ErrorCode.PLUGIN_INCOMPATIBLE);
      const features: unknown = params['features'] ?? [];
      adapter.batches = Array.isArray(features) && features.includes('event-batches-v1');
      return metadata;
    }
    if (method === 'health') {
      const health = { ok: true };
      return health;
    }
    if (method === 'invoke' && params['capability'] === 'ai.decide') {
      if (invoking) throw new FrameworkError(ErrorCode.RUN_BUSY);
      invoking = true;
      try {
        return await adapter.decide(object(params['input']));
      } finally {
        invoking = false;
      }
    }
    throw new FrameworkError(ErrorCode.METHOD_NOT_FOUND);
  },
);
const adapter: CodexAdapter = new CodexAdapter(peer);
try {
  await peer.done;
} catch (error) {
  process.stderr.write(`${JSON.stringify(frameworkError(error).details())}\n`);
  process.exitCode = 1;
} finally {
  await adapter.close();
  await peer.close();
}
