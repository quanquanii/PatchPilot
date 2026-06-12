# Calculator — Requirements

## Feature: add(a, b)

### Functional Requirements

1. `add(a, b)` should return the sum of `a` and `b`.
2. Inputs are expected to be numbers.
3. The function should support both integers and floats.
4. The function should return a single numeric value.

### Unclear / Open Questions

- Behavior for non-number inputs (e.g., strings, None, lists) is not specified.
- No overflow or precision expectations are defined for large floats.
- It is unclear whether the function should raise an exception or return a default value for invalid inputs.
- No documentation requirement is stated.

### Out of Scope (assumed)

- Input validation is not mentioned; it is unclear if it is expected.
- Logging, metrics, or observability are not mentioned.
- Performance requirements are not stated.
