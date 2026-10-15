// A saved binding is immutable here. Keep conflicting edits available for an
// explicit recovery instead of silently submitting a different identity.
export function datasetIdentityConflict(saved, values) {
  return Boolean(saved?.externalId && (
    String(saved.externalId) !== String(values?.externalId ?? '') ||
    (saved.source && saved.source !== values?.source)
  ));
}
