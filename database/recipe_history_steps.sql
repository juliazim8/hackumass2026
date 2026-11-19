ALTER TABLE public.recipes
    ADD COLUMN IF NOT EXISTS steps jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.recipes
    ADD COLUMN IF NOT EXISTS ingredients jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.recipes
    ADD COLUMN IF NOT EXISTS last_made_at timestamp with time zone NOT NULL DEFAULT now();
