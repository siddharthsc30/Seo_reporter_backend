-- ============================================================================
-- MONTHLY SEO REPORTS DATABASE MIGRATION
-- Table: public.monthly_reports
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.monthly_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    website TEXT NOT NULL,
    company_name TEXT,
    company_base_country TEXT,
    report_month TEXT NOT NULL, -- Format: "YYYY-MM" (e.g. "2026-08") or custom "YYYY-MM-DD_to_YYYY-MM-DD"
    period_label TEXT,          -- Format: "1–31 August 2026, compared against 1–31 July 2026"
    primary_period_label TEXT,
    comparison_period_label TEXT,
    status TEXT DEFAULT 'draft', -- 'draft' | 'published'
    share_token TEXT UNIQUE,     -- High-entropy unlisted URL token
    agency_branding JSONB DEFAULT '{"agency_name": "SEO Reporter", "accent_color": "#6366f1"}'::jsonb,
    metrics_data JSONB DEFAULT '{}'::jsonb,
    narrative_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    UNIQUE(user_id, website, report_month)
);

-- Idempotent schema migrations if table already partially exists
ALTER TABLE public.monthly_reports ADD COLUMN IF NOT EXISTS company_base_country TEXT;
ALTER TABLE public.monthly_reports ADD COLUMN IF NOT EXISTS primary_period_label TEXT;
ALTER TABLE public.monthly_reports ADD COLUMN IF NOT EXISTS comparison_period_label TEXT;

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_monthly_reports_user_id ON public.monthly_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_monthly_reports_share_token ON public.monthly_reports(share_token);
CREATE INDEX IF NOT EXISTS idx_monthly_reports_user_website ON public.monthly_reports(user_id, website);

-- Enable Row Level Security (RLS)
ALTER TABLE public.monthly_reports ENABLE ROW LEVEL SECURITY;

-- Policy 1: Authenticated User has full CRUD over their own reports
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'monthly_reports' 
        AND policyname = 'Allow owner full access on monthly_reports'
    ) THEN
        CREATE POLICY "Allow owner full access on monthly_reports" ON public.monthly_reports
          FOR ALL TO authenticated
          USING (auth.uid() = user_id)
          WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;

-- Policy 2: Public unauthenticated read allowed for published reports via share_token
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies 
        WHERE tablename = 'monthly_reports' 
        AND policyname = 'Allow public read on published monthly_reports'
    ) THEN
        CREATE POLICY "Allow public read on published monthly_reports" ON public.monthly_reports
          FOR SELECT TO anon, authenticated
          USING (status = 'published' AND share_token IS NOT NULL);
    END IF;
END $$;

