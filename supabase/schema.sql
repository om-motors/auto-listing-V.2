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

-- Einen Mehrteile-Upload ohne Zwischenzustand trennen. Das UPDATE des
-- Elternauftrags und alle INSERTs laufen als ein Funktionsaufruf in derselben
-- Postgres-Transaktion. Deterministische Kind-IDs machen Wiederholungen nach
-- einem verlorenen Netzwerk-Reply idempotent.
create or replace function public.auftrag_atomar_aufteilen(
  p_auftrag_id uuid,
  p_eltern_fotos text[],
  p_kinder jsonb
) returns void
language plpgsql
security invoker
set search_path = public
as $$
begin
  update public.auftraege
     set fotos = p_eltern_fotos
   where id = p_auftrag_id and status = 'laeuft';
  if not found then
    raise exception 'Auftrag % ist nicht im Status laeuft', p_auftrag_id;
  end if;

  insert into public.auftraege (id, fotos, bezeichnung)
  select (kind->>'id')::uuid,
         array(select jsonb_array_elements_text(kind->'fotos')),
         null
    from jsonb_array_elements(p_kinder) as kind
  on conflict (id) do update set fotos = excluded.fotos;
end;
$$;

revoke all on function public.auftrag_atomar_aufteilen(uuid, text[], jsonb)
  from public, anon, authenticated;
grant execute on function public.auftrag_atomar_aufteilen(uuid, text[], jsonb)
  to service_role;
grant select, insert, update on table public.auftraege to service_role;

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
