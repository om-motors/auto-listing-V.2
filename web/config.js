// Zugangsdaten der Web-App — die EINZIGE Datei, die du von Hand ausfüllst.
//
// Beide Werte findest du in Supabase unter:
//   Project Settings  ->  API Keys
//
//   SUPABASE_URL      = "Project URL"
//   SUPABASE_ANON_KEY = der Schlüssel "anon" / "public" / "publishable"
//
// Beide sind öffentlich und dürfen hier stehen — die Seite ist im Netz
// abrufbar, und genau deshalb schützt nicht der Schlüssel den Zugang, sondern
// die Anmeldung plus die Zugriffsregeln (RLS) aus supabase/schema.sql.
//
// ACHTUNG: Der Schlüssel "service_role" / "secret" gehört NIEMALS hierher.
// Der umgeht alle Zugriffsregeln und darf ausschließlich in die .env auf dem
// Mac. Wer ihn hier einträgt, gibt jedem Besucher der Seite volle Rechte auf
// die Datenbank.

window.AUTOLISTING_CONFIG = {
  SUPABASE_URL: "https://dsjfxlxqskhcezmsvafx.supabase.co",
  SUPABASE_ANON_KEY: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRzamZ4bHhxc2toY2V6bXN2YWZ4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU2ODAyMTgsImV4cCI6MjEwMTI1NjIxOH0.0i2Cm6Da0lovCQcR7mT4evtzsLQnlU52AIOX6Qzp-NU",
};
