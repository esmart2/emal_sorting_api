-- Add google_sub column to raw_emails
ALTER TABLE public.raw_emails
ADD COLUMN google_sub TEXT NOT NULL;

-- Add foreign key constraint to raw_emails
ALTER TABLE public.raw_emails
ADD CONSTRAINT fk_raw_emails_gmail_account
FOREIGN KEY (user_id, google_sub)
REFERENCES public.gmail_accounts(user_id, google_sub)
ON DELETE CASCADE;

-- Add google_sub column to processed_emails
ALTER TABLE public.processed_emails
ADD COLUMN google_sub TEXT NOT NULL;

-- Add foreign key constraint to processed_emails
ALTER TABLE public.processed_emails
ADD CONSTRAINT fk_processed_emails_gmail_account
FOREIGN KEY (user_id, google_sub)
REFERENCES public.gmail_accounts(user_id, google_sub)
ON DELETE CASCADE;

-- Update primary keys to include google_sub
ALTER TABLE public.raw_emails
DROP CONSTRAINT raw_emails_pkey,
ADD PRIMARY KEY (user_id, gmail_message_id, thread_id, google_sub);

ALTER TABLE public.processed_emails
DROP CONSTRAINT processed_emails_pkey,
ADD PRIMARY KEY (user_id, gmail_message_id, thread_id, google_sub); 