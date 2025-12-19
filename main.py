import functions_framework
import os
import json
from datetime import datetime, timedelta
from google.cloud import storage
import google.generativeai as genai
import requests

# ============================================================================
# Constants
# ============================================================================

MODEL_NAME = 'gemini-3-flash-preview'
FINNHUB_KEY = 'd50n6bhr01qm94qn135gd50n6bhr01qm94qn1360'
BUCKET_NAME = 'your-market-brief-bucket'  # Replace with your actual bucket name


# ============================================================================
# Helper Functions
# ============================================================================

def get_formatted_date(date=None):
    """Format date to 'long' format matching JavaScript toLocaleDateString"""
    if date is None:
        date = datetime.now()
    return date.strftime('%A, %B %d, %Y')


def get_previous_trading_day_str(date=None):
    """
    Calculate the previous trading day (excluding weekends).
    Matches the JavaScript logic exactly.
    """
    if date is None:
        date = datetime.now()
    
    day = date.weekday()  # Monday=0, Sunday=6
    prev = date
    
    if day == 0:  # Monday
        prev = date - timedelta(days=3)  # Go back to Friday
    elif day == 6:  # Sunday
        prev = date - timedelta(days=2)  # Go back to Friday
    elif day == 5:  # Saturday
        prev = date - timedelta(days=1)  # Go back to Friday
    else:  # Tuesday-Friday
        prev = date - timedelta(days=1)  # Go back one day
    
    return prev.strftime('%A, %B %d, %Y')


def fetch_finnhub_metrics(ticker):
    """
    Fetch market data from Finnhub API.
    Returns FinnhubData object or None if data unavailable.
    """
    try:
        quote_url = f'https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_KEY}'
        profile_url = f'https://finnhub.io/api/v1/stock/profile2?symbol={ticker}&token={FINNHUB_KEY}'
        
        quote_resp = requests.get(quote_url ).json()
        profile_resp = requests.get(profile_url).json()
        
        if not quote_resp.get('c') or not profile_resp.get('marketCapitalization'):
            return None
        
        market_cap = profile_resp.get('marketCapitalization')  # in millions
        price_change = quote_resp.get('dp')  # percentage
        
        return {
            'ticker': ticker,
            'name': profile_resp.get('name', ticker),
            'marketCap': market_cap,
            'priceChange': price_change,
            'isMegaCapMover': market_cap >= 200000 and abs(price_change) >= 2.0
        }
    except Exception as e:
        print(f"Error fetching Finnhub data for {ticker}: {e}")
        return None


def generate_market_brief():
    """
    Generate market brief using Google Generative AI.
    Preserves all logic and specifications from the React frontend.
    """
    try:
        api_key = os.environ.get('GOOGLE_API_KEY')
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not set")
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL_NAME)
        
        target_date = get_previous_trading_day_str()
        display_date = get_formatted_date()
        
        # =====================================================================
        # Step 1: Discovery - Identify market movers
        # =====================================================================
        print("Step 1: Searching for market mover candidates...")
        discovery_prompt = f"""Identify 15 major public companies (mix of North America, Europe, Asia) that were top news drivers or price movers for {target_date}. 
Include tech giants like Apple, Microsoft, NVIDIA, Amazon, Google, Meta, Tesla, etc.
Return ONLY a JSON array of tickers like ["AAPL", "MSFT", "TSLA", "NVDA", "ASML", "MC.PA", "7203.T", "SAP"]."""
        
        discovery_resp = model.generate_content(
            discovery_prompt,
            tools=[genai.Tool(google_search=genai.GoogleSearch())]
        )
        
        tickers = []
        try:
            cleaned = discovery_resp.text.replace('```json', '').replace('```', '').strip()
            tickers = json.loads(cleaned)
        except Exception as e:
            raise ValueError(f"Could not parse ticker candidates: {e}")
        
        # =====================================================================
        # Step 2: Verification - Check Mega-Cap Momentum Rule
        # =====================================================================
        print("Step 2: Verifying Market Cap & Price Change via Finnhub...")
        verified_movers = []
        for ticker in tickers:
            data = fetch_finnhub_metrics(ticker)
            if data:
                verified_movers.append(data)
        
        mega_cap_movers = [m for m in verified_movers if m['isMegaCapMover']]
        
        # =====================================================================
        # Step 3: Synthesis - Generate professional report
        # =====================================================================
        print("Step 3: Synthesizing professional report...")
        
        mandatory_context = ""
        if mega_cap_movers:
            movers_str = ", ".join([
                f"{m['name']} ({'+' if m['priceChange'] > 0 else ''}{m['priceChange']:.2f}%)"
                for m in mega_cap_movers
            ])
            mandatory_context = f"""The following companies MUST be included as they meet the Mega-Cap Momentum Rule (Cap > $200B and Change >= ±2%): 
{movers_str}."""
        
        prompt = f"""As a senior institutional analyst, generate the "Market News" for {display_date}.
Performance figures must reflect the close of {target_date}.

{mandatory_context}

STRUCTURE AND MANDATORY INDEX REPORTING:
## North America
The VERY FIRST bullet point MUST be a consolidated overview of the major indices (S&P 500, Nasdaq, Dow Jones) in ONE single bullet point.
Example:
* **Market Indices**: The **S&P 500 (-1.2%)** fell to 6,721.43, while the **Nasdaq Composite (-1.8%)** led declines at 22,693.32, and the **Dow Jones Industrial Average (-0.5%)** proved more resilient, finishing at 47,885.97.
Then follow with 6-8 more bullets for individual company movers.

## Europe
* 5-6 Bullets focusing solely on company news. Do NOT include a market index overview bullet point here.

## Asia, Emerging Markets & Commodities
The VERY FIRST bullet point MUST be a consolidated overview of the major regional indices (Nikkei 225, Hang Seng) in ONE single bullet point.
Example:
* **Regional Indices**: The **Nikkei 225 (+0.4%)** advanced on yen weakness, while the **Hang Seng Index (-1.1%)** retreated amid property sector concerns.
Then follow with 4-6 more bullets for regional company movers and commodities like Oil and Gold.

RULES:
- Every bullet point MUST be between 115 and 250 characters. Do not exceed 250 characters.
- Bold the first entity or the "Market Indices"/"Regional Indices" header.
- Tone: Factual, institutional, dense. No emojis.
- ALWAYS include % change in parentheses: (**Entity Name (+2.1%)**).
- Ground everything in web search for accurate catalysts and closing prices for {target_date}."""
        
        response = model.generate_content(
            prompt,
            tools=[genai.Tool(google_search=genai.GoogleSearch())],
            generation_config={'temperature': 0.05}
        )
        
        brief_content = response.text
        
        # Extract grounding sources
        sources = []
        try:
            grounding_metadata = response.candidates[0].grounding_metadata if response.candidates else None
            if grounding_metadata and hasattr(grounding_metadata, 'grounding_chunks'):
                source_map = {}
                for chunk in grounding_metadata.grounding_chunks:
                    if hasattr(chunk, 'web') and chunk.web:
                        if hasattr(chunk.web, 'uri') and hasattr(chunk.web, 'title'):
                            source_map[chunk.web.uri] = chunk.web.title
                sources = [{'uri': uri, 'title': title} for uri, title in source_map.items()]
        except Exception as e:
            print(f"Error extracting sources: {e}")
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            'status': 'success',
            'content': brief_content,
            'sources': sources,
            'timestamp': timestamp,
            'displayDate': display_date,
            'targetDate': target_date
        }
    
    except Exception as e:
        print(f"Error generating brief: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }


def save_to_cloud_storage(brief_data):
    """Save the generated brief to Cloud Storage"""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        filename = f"market_brief_{timestamp}.json"
        
        data_to_save = {
            'displayDate': brief_data.get('displayDate'),
            'targetDate': brief_data.get('targetDate'),
            'generatedAt': brief_data.get('timestamp'),
            'content': brief_data.get('content'),
            'sources': brief_data.get('sources', []),
            'status': brief_data.get('status')
        }
        
        blob = bucket.blob(filename)
        blob.upload_from_string(
            json.dumps(data_to_save, indent=2),
            content_type='application/json'
        )
        
        return {
            'success': True,
            'bucket': BUCKET_NAME,
            'filename': filename
        }
    
    except Exception as e:
        print(f"Error saving to Cloud Storage: {e}")
        return {
            'success': False,
            'error': str(e)
        }


# ============================================================================
# Cloud Function Entry Point
# ============================================================================

@functions_framework.http
def generate_market_brief_http(request ):
    """
    HTTP Cloud Function that generates a market brief and stores it.
    Entry point for Cloud Scheduler triggers.
    """
    
    brief_data = generate_market_brief()
    
    if brief_data.get('status') == 'success':
        storage_result = save_to_cloud_storage(brief_data)
        brief_data['storage'] = storage_result
    
    status_code = 200 if brief_data.get('status') == 'success' else 500
    
    return {
        'status': brief_data.get('status'),
        'message': brief_data.get('error') or 'Market brief generated successfully',
        'timestamp': brief_data.get('timestamp'),
        'displayDate': brief_data.get('displayDate'),
        'targetDate': brief_data.get('targetDate'),
        'sources': brief_data.get('sources', []),
        'storage': brief_data.get('storage', {})
    }, status_code
