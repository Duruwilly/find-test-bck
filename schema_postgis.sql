-- Production schema (Postgres + PostGIS). The dev app uses plain lat/lng columns so it
-- runs on SQLite; in prod, add the generated geography column below for spatial queries
-- (e.g. B2B "cards within 500m of X").

CREATE EXTENSION IF NOT EXISTS postgis;

-- After SQLAlchemy creates the tables:
ALTER TABLE cards
  ADD COLUMN IF NOT EXISTS location geography(Point, 4326)
  GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography) STORED;

CREATE INDEX IF NOT EXISTS idx_cards_location ON cards USING GIST (location);

-- Example: cards within 300m of a point
-- SELECT short_code, title FROM cards
-- WHERE ST_DWithin(location, ST_SetSRID(ST_MakePoint(3.4723, 6.4478), 4326)::geography, 300);
