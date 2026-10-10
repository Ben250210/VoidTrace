-- VoidTrace shared price cache. Holds only public Warframe.Market aggregates.
create table if not exists public.price_cache (
    slug        text primary key,
    median      double precision not null,
    q1          double precision not null,
    q3          double precision not null,
    low         double precision not null,
    high        double precision not null,
    n_orders    integer not null,
    n_kept      integer not null,
    n_outliers  integer not null,
    source      text not null check (source in ('ingame', 'online', 'all')),
    fetched_at  double precision not null  -- unix seconds, matches PriceStats.fetched_at
);

alter table public.price_cache enable row level security;

-- The desktop client uses the publishable (anon) key. The data is public market
-- aggregates, so anon may read and upsert, but not delete.
create policy "price_cache read" on public.price_cache
    for select to anon using (true);
create policy "price_cache insert" on public.price_cache
    for insert to anon with check (true);
create policy "price_cache update" on public.price_cache
    for update to anon using (true) with check (true);
