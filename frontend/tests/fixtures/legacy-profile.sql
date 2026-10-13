-- Seeds the retired SQLite Profile format for ProfileRepository migration tests.
-- Production no longer creates these tables; migration must preserve the original identity and Soul.
create table profiles (
  id text primary key, display_name text not null, active integer not null,
  created_at text not null, updated_at text not null
);
create table souls (
  id text primary key, profile_id text not null unique,
  name text not null, persona text not null, tone text not null,
  proactive_level text not null, principles_json text not null, taboos_json text not null,
  reminder_style text not null, reply_preference text not null, speaking_style text not null,
  relationship text not null, boundaries_json text not null, topic_preferences_json text not null,
  created_at text not null, updated_at text not null
);
insert into profiles values ('profile_default', 'Aura', 1, '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z');
insert into souls values (
  'soul_default', 'profile_default', 'Aura', 'Companion', 'Warm', 'balanced', '["Be reliable"]', '[]',
  '', '', 'Natural', 'Friend', '["Respect privacy"]', '["daily life"]', '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z'
);
