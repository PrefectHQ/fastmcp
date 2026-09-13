Fixes #5070

## Changes

- `CursorState.decode()` now validates that the offset field ("o") is a non-negative integer
- Previously, string, float, negative, and boolean offsets were silently accepted, causing internal server errors (for non-integer types) or incorrect pagination results (for negative offsets)
- Added type validation: offset must be an int (rejects bool, float, str, None)
- Added range validation: offset must be >= 0

## Testing

- Added 5 new test cases in TestCursorEncoding:
  - test_decode_string_offset_raises - string offsets rejected
  - test_decode_float_offset_raises - float offsets rejected
  - test_decode_negative_offset_raises - negative offsets rejected
  - test_decode_boolean_offset_raises - boolean offsets rejected
  - test_decode_null_offset_raises - null offsets rejected
- All existing pagination tests continue to pass
