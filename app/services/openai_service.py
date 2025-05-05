from typing import List, Dict, Any, Tuple
from openai import OpenAI
import os
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

class OpenAIService:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    def extract_text_from_html(self, html_content: str) -> str:
        """
        Extract clean text from HTML content using BeautifulSoup.
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            # Get text and clean up whitespace
            text = soup.get_text(separator=' ', strip=True)
            # Remove excessive whitespace
            text = ' '.join(text.split())
            return text
        except Exception:
            # If HTML parsing fails, return the original content
            return html_content

    def create_categorization_prompt(self, email: Dict[str, Any], categories: List[Dict[str, Any]]) -> str:
        """
        Create a detailed prompt for GPT to categorize a single email and provide a summary.
        """
        # Create category description string
        category_descriptions = "\n".join([
            f"Category {cat['id']}: '{cat['name']}' - {cat['description'] or 'No description'}"
            for cat in categories
        ])

        # Extract clean text from email body
        clean_body = self.extract_text_from_html(email['body'])
        
        # Truncate body if too long (keeping first 1000 characters)
        truncated_body = clean_body[:1000] + "..." if len(clean_body) > 1000 else clean_body

        # Construct the full prompt
        prompt = f"""As an expert email analyzer, your task is to:
1. Categorize the email into exactly one of the provided categories
2. Provide a brief summary (max 2-3 sentences) of the email's key points

Available Categories:
{category_descriptions}

Email Content:
Subject: {email['subject']}
Body: {truncated_body}

Respond in this exact JSON format:
{{
    "category_id": <integer>,
    "summary": "<2-3 sentence summary>",
    "confidence": <float between 0 and 1>
}}

The confidence score should reflect how certain you are about the categorization.
Provide only the JSON object, no additional text."""

        return prompt

    async def categorize_email(self, email: Dict[str, Any], categories: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Use GPT-4 to categorize a single email and generate a summary.
        """
        try:
            prompt = self.create_categorization_prompt(email, categories)
            
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an expert email analyzer that categorizes emails and provides brief summaries."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent results
                response_format={ "type": "json_object" }  # Ensure JSON response
            )

            # Parse the response
            result = eval(response.choices[0].message.content)
            
            # Add the email ID to the result
            result["gmail_message_id"] = email["gmail_message_id"]
            
            return result

        except Exception as e:
            raise Exception(f"Error analyzing email with OpenAI: {str(e)}") 