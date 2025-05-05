-- Add email column to gmail_accounts
ALTER TABLE public.gmail_accounts
ADD COLUMN email TEXT NOT NULL;

-- Create an index on email for faster lookups
CREATE INDEX idx_gmail_accounts_email ON public.gmail_accounts(email);

-- Add a unique constraint to prevent duplicate email addresses per user
ALTER TABLE public.gmail_accounts
ADD CONSTRAINT unique_user_email UNIQUE (user_id, email); 