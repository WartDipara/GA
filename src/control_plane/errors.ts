import { ErrorCode, errorMessages } from '../../var/cache/typescript/generated/errors.js';

export { ErrorCode };

export class FrameworkError extends Error {
  constructor(
    readonly code: ErrorCode,
    message = errorMessages[code],
    readonly retryable = false,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = 'FrameworkError';
  }

  details(): { code: ErrorCode; message: string; retryable: boolean } {
    const details = { code: this.code, message: this.message, retryable: this.retryable };
    return details;
  }
}

export function frameworkError(error: unknown): FrameworkError {
  if (error instanceof FrameworkError) return error;
  const name = error instanceof Error ? error.name : 'Unknown thrown value';
  return new FrameworkError(ErrorCode.INTERNAL_ERROR, name, false, { cause: error });
}
