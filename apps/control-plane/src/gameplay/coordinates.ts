import { ErrorCode, FrameworkError } from '@game-automation/contracts/errors';
import type { Action, Coordinates } from './types.js';

type Bounds = NonNullable<Action['bounds']>;

export function rectangle(bounds: Bounds, coordinates: Coordinates, native = false): Bounds {
  if (
    native &&
    !(
      bounds[0] >= 0 &&
      bounds[1] >= 0 &&
      bounds[0] < bounds[2] &&
      bounds[1] < bounds[3] &&
      bounds[2] <= coordinates.width &&
      bounds[3] <= coordinates.height
    )
  )
    throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Target is outside the planner image');
  const x = native
    ? coordinates.native_width / coordinates.width
    : coordinates.width / coordinates.native_width;
  const y = native
    ? coordinates.native_height / coordinates.height
    : coordinates.height / coordinates.native_height;
  const result: Bounds = [
    Math.floor(bounds[0] * x),
    Math.floor(bounds[1] * y),
    Math.ceil(bounds[2] * x),
    Math.ceil(bounds[3] * y),
  ];
  return result;
}

export function projectAction(
  action: Action | null,
  coordinates: Coordinates,
  native = false,
): Action | null {
  if (!action) return null;
  const point = (x: number, y: number): [number, number] => {
    if (native && (x < 0 || y < 0 || x >= coordinates.width || y >= coordinates.height))
      throw new FrameworkError(ErrorCode.AI_OUTPUT_INVALID, 'Point is outside the planner image');
    const result: [number, number] = [
      Math.floor(
        x *
          (native
            ? coordinates.native_width / coordinates.width
            : coordinates.width / coordinates.native_width),
      ),
      Math.floor(
        y *
          (native
            ? coordinates.native_height / coordinates.height
            : coordinates.height / coordinates.native_height),
      ),
    ];
    return result;
  };
  const [x, y] = point(action.x, action.y);
  const [end_x, end_y] = point(action.end_x, action.end_y);
  const result = {
    ...action,
    x,
    y,
    end_x,
    end_y,
    bounds: action.bounds ? rectangle(action.bounds, coordinates, native) : null,
  };
  return result;
}
