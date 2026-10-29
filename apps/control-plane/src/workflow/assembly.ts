import { decode, type ContractMap } from '@game-automation/contracts';
import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';

export interface Stage {
  name: string;
  mode: string | null;
  requires: string[];
  perApk: boolean;
  capabilities: string[];
  revalidation?: 'installation' | 'foreground';
}

export class WorkflowBuilder {
  private readonly stages: Stage[] = [];

  add(stage: Stage): this {
    this.stages.push(stage);
    return this;
  }

  build(): Stage[] {
    const available = new Set<string>();
    const itemStages = new Set<string>();
    let itemGroupClosed = false;
    for (const stage of this.stages) {
      const missing = stage.requires.filter((name) => !available.has(name));
      if (available.has(stage.name) || missing.length)
        throw new FrameworkError(
          ErrorCode.WORKFLOW_INVALID,
          `Stage ${stage.name}: duplicate or missing dependencies ${missing.join(', ')}`,
        );
      if (stage.perApk) {
        if (itemGroupClosed)
          throw new FrameworkError(
            ErrorCode.WORKFLOW_INVALID,
            'APK stages must form one contiguous group',
          );
        itemStages.add(stage.name);
      } else {
        if (stage.requires.some((name) => itemStages.has(name)))
          throw new FrameworkError(
            ErrorCode.WORKFLOW_INVALID,
            'Batch stages cannot consume one APK output',
          );
        itemGroupClosed = itemStages.size > 0;
      }
      available.add(stage.name);
    }
    if (!available.size)
      throw new FrameworkError(ErrorCode.WORKFLOW_INVALID, 'Select at least one stage');
    const stages = [...this.stages];
    return stages;
  }
}

export function buildWorkflow(value: unknown): { task: ContractMap['workflow']; stages: Stage[] } {
  const task = decode('workflow', value);
  const modules: Stage[] = [
    {
      name: 'preprocess',
      mode: 'preprocessing',
      requires: [],
      perApk: false,
      capabilities: ['apk.preflight', 'apk.plan', 'apk.prepare'],
    },
    {
      name: 'environment',
      mode: 'emulators',
      requires: ['preprocess'],
      perApk: true,
      capabilities: ['emulator.discover'],
    },
    {
      name: 'prepare',
      revalidation: 'installation',
      mode: 'device',
      requires: ['preprocess', 'environment'],
      perApk: true,
      capabilities: ['emulator.session', 'device.inspect', 'device.install'],
    },
    {
      name: 'launch',
      revalidation: 'foreground',
      mode: 'gameplay',
      requires: ['prepare'],
      perApk: true,
      capabilities: [
        'emulator.session',
        'device.inspect',
        'device.runtime',
        'device.capture',
        'device.execute',
        'device.logs',
        'credential.prepare',
      ],
    },
    {
      name: 'play',
      mode: 'gameplay',
      requires: ['prepare', 'launch'],
      perApk: true,
      capabilities: [
        'ai.decide',
        'perception.analyze',
        'perception.locate',
        'perception.viewport',
        'planner.image',
      ],
    },
    { name: 'report', mode: null, requires: [], perApk: false, capabilities: [] },
  ];
  const registered = new Map(modules.map((stage) => [stage.name, stage]));
  const builder = new WorkflowBuilder();
  for (const name of task.stages) {
    const stage = registered.get(name);
    if (!stage) throw new FrameworkError(ErrorCode.WORKFLOW_INVALID, `Unknown stage: ${name}`);
    builder.add(stage);
  }
  const stages = builder.build();
  if (
    stages.some((stage) => stage.perApk) &&
    (task.instances.length !== task.source_ids.length ||
      new Set(task.instances).size !== task.instances.length)
  )
    throw new FrameworkError(
      ErrorCode.WORKFLOW_INVALID,
      'Assign one distinct MuMu instance to each APK',
    );
  const workflow = { task, stages };
  return workflow;
}
