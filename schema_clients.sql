-- ============================================================================
-- CLIENTS MANAGEMENT TABLE MIGRATION
-- Table: public.clients
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    website TEXT NOT NULL,
    industry TEXT,
    logo_url TEXT,
    contact_name TEXT,
    contact_email TEXT,
    notes TEXT,
    ga4_property_id TEXT,
    ga4_property_name TEXT,
    gsc_site_url TEXT,
    google_account_email TEXT,
    google_refresh_token TEXT,
    semrush_project_id TEXT,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Schema Migrations if columns do not exist
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS ga4_property_name TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_account_email TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS google_refresh_token TEXT;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS semrush_project_id TEXT;

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_clients_user_id ON public.clients(user_id);
CREATE INDEX IF NOT EXISTS idx_clients_website ON public.clients(website);

-- Enable Row Level Security (RLS)
ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;

-- Policy: Authenticated User has full CRUD over their own clients
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'clients' 
        AND policyname = 'Allow owner full access on clients'
    ) THEN
        CREATE POLICY "Allow owner full access on clients" ON public.clients
          FOR ALL TO authenticated
          USING (auth.uid() = user_id)
          WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;
