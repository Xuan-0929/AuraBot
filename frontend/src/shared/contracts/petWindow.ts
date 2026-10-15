/** Defines desktop Pet default and minimum logical-pixel dimensions.
 * Main uses the minimum for creation, restored bounds and edge resizing; the Vue shell consumes
 * the same values so its layout can shrink with the native window without hidden overflow.
 */
export const PET_WINDOW_DEFAULT_SIZE = {
  width: 300,
  height: 430
} as const;

// Shared with the Vue shell; default window dimensions remain independent.
export const PET_WINDOW_MIN_SIZE = {
  width: 135,
  height: 190
} as const;
