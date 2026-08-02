-- Auto-Listing — Datenmodell für die Web-App
--
-- Einmal im Supabase-SQL-Editor ausführen (Projekt -> SQL Editor -> New query).
-- Läuft mehrfach ohne Schaden, alles ist "if not exists" / "on conflict".
--
-- Rollenverteilung:
--   * Die Web-Seite auf Netlify nutzt den **anon key** und muss sich anmelden.
--     Sie darf Aufträge anlegen und den eigenen Stand lesen — mehr nicht.
--   * Der Mac nutzt den **service key**. Der umgeht RLS und darf alles.
--     Dieser Schlüssel gehört ausschließlich in die .env auf dem Mac,
--     NIEMALS in die Web-Seite — sie ist öffentlich abrufbar.

-- ---------------------------------------------------------------- Aufträge --

create table if not exists public.auftraege (
  id            uuid primary key default gen_random_uuid(),
  erstellt_am   timestamptz not null default now(),

  -- neu    = wartet auf den Mac
  -- laeuft = der Mac arbeitet gerade daran
  -- fertig = Entwurf liegt bei eBay
  -- fehler = abgebrochen, Grund steht in `fehler`
  status        text not null default 'neu'
                check (status in ('neu', 'laeuft', 'fertig', 'fehler')),

  -- Optionale Eingabe vom Handy. Wer die Teilenummer kennt, tippt sie hier
  -- ein — dann muss die Texterkennung sie nicht aus dem Foto holen.
  bezeichnung   text,

  -- Pfade der Fotos im Storage-Bucket "fotos"
  fotos         text[] not null default '{}',

  begonnen_am   timestamptz,
  fertig_am     timestamptz,

  -- Ergebnis, damit das Handy es anzeigen kann
  titel         text,
  preis         numeric(10, 2),
  versandstufe  text,
  entwurf_url   text,
  bericht       text,      -- der komplette Markdown-Bericht
  offene_punkte text[],    -- was laut Bericht noch von Hand zu tun ist
  fehler        text
);

create index if not exists auftraege_status_idx
  on public.auftraege (status, erstellt_am);

alter table public.auftraege enable row level security;

-- Angemeldete Nutzer (also du) dürfen alles sehen und anlegen. Das Werkzeug
-- hat genau einen Benutzer; eine feinere Trennung wäre Ballast.
drop policy if exists "angemeldete duerfen lesen" on public.auftraege;
create policy "angemeldete duerfen lesen"
  on public.auftraege for select to authenticated using (true);

drop policy if exists "angemeldete duerfen anlegen" on public.auftraege;
create policy "angemeldete duerfen anlegen"
  on public.auftraege for insert to authenticated with check (true);

-- Bewusst KEINE update/delete-Regel für `authenticated`: Ergebnisse schreibt
-- nur der Mac (service key). So kann die Web-Seite einen laufenden Auftrag
-- nicht versehentlich überschreiben.

-- ----------------------------------------------------------------- Storage --

insert into storage.buckets (id, name, public)
values ('fotos', 'fotos', false)
on conflict (id) do nothing;

drop policy if exists "angemeldete duerfen hochladen" on storage.objects;
create policy "angemeldete duerfen hochladen"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'fotos');

drop policy if exists "angemeldete duerfen fotos lesen" on storage.objects;
create policy "angemeldete duerfen fotos lesen"
  on storage.objects for select to authenticated
  using (bucket_id = 'fotos');

-- Der Bucket ist NICHT öffentlich. Die Fotos zeigen Teile aus Fahrzeugen und
-- gehen niemanden etwas an, der die Netlify-Adresse zufällig findet.
-- Der Mac löscht sie nach der Verarbeitung, damit das Gratis-Kontingent
-- (1 GB) nicht vollläuft.
