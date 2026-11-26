import { decode, object, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Fields } from '../utilities/values.js';

type Settings = ContractMap['gameplay-agent-settings'][string];
export type GameplayJob = Settings['job_title'];
export type GameplayAgent = Settings & { id: string };

export class AgentDirectory {
  private readonly jobs = new Map<GameplayJob, GameplayAgent>();

  constructor(manifest: Fields) {
    const application = object(manifest['application']);
    const gameplay = object(application['gameplay']);
    const settings = decode('gameplay-agent-settings', gameplay['agents']);
    for (const [id, config] of Object.entries(settings)) {
      if (this.jobs.has(config.job_title))
        throw new FrameworkError(
          ErrorCode.INVALID_CONTRACT, `Duplicate gameplay job title: ${config.job_title}`,
        );
      this.jobs.set(config.job_title, { ...config, id });
    }
  }

  agent(job: GameplayJob): GameplayAgent {
    const agent = this.jobs.get(job);
    if (!agent)
      throw new FrameworkError(ErrorCode.INVALID_CONTRACT, `Missing gameplay job title: ${job}`);
    return agent;
  }
}
