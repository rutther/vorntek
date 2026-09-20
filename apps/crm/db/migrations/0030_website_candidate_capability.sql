ALTER TABLE content_access_grant
  DROP CONSTRAINT IF EXISTS content_access_grant_capability_check;

ALTER TABLE content_access_grant
  ADD CONSTRAINT content_access_grant_capability_check CHECK (
    capability IN (
      'content.read',
      'content.write',
      'content.set_published',
      'content.locale.manage',
      'assets.read',
      'assets.write',
      'assets.import_local',
      'releases.read',
      'releases.preview_build',
      'releases.candidate_build'
    )
  );
