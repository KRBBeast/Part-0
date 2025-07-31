from flask import Flask, request, jsonify, render_template_string, render_template
from flask_cors import CORS
import requests
import re
import json
import time
import logging
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import os
from supabase import create_client, Client

# Configuração de logging detalhado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('proscraper.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuração Supabase
SUPABASE_URL = "https://cnwrcrpihldqejyvgysn.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNud3JjcnBpaGxkcWVqeXZneXNuIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1MTU5NzM3OSwiZXhwIjoyMDY3MTczMzc5fQ.GdZPPZvSAslVIgzbmq8rhstK94qxh7WUwH623GUvb4g"

# ScraperAPI Configuration
SCRAPER_API_KEY = os.getenv('SCRAPER_API_KEY', '2db7bcd2b42c895deb472a3da5aab3e7')
SCRAPER_API_URL = "http://api.scraperapi.com"

@dataclass
class ProductData:
    url: str
    title: Optional[str] = None
    price_current: Optional[float] = None
    price_original: Optional[float] = None
    price_current_text: Optional[str] = None
    price_original_text: Optional[str] = None
    image_url: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    condition: Optional[str] = None
    sold_quantity: Optional[str] = None
    best_seller_position: Optional[str] = None
    free_shipping: Optional[bool] = None
    shipping_info: Optional[str] = None
    currency: str = "BRL"
    discount_percentage: Optional[float] = None
    extraction_time: Optional[float] = None
    site_name: Optional[str] = None
    errors: Optional[List[str]] = None

class ProScraper:
    def __init__(self):
        self.session = self._create_robust_session()
        
        # Cliente Supabase
        try:
            self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("Supabase conectado com sucesso")
        except Exception as e:
            logger.error(f"Erro ao conectar Supabase: {e}")
            self.supabase = None

    def _create_robust_session(self) -> requests.Session:
        """Cria sessão robusta com headers completos"""
        session = requests.Session()
        
        # Headers completos para parecer um navegador real
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"'
        }
        
        session.headers.update(headers)
        session.timeout = 30
        
        return session

    def _make_scraperapi_request(self, url: str, max_retries: int = 3) -> Optional[str]:
        """Faz requisição usando ScraperAPI com múltiplas tentativas"""
        for attempt in range(max_retries):
            try:
                logger.info(f"ScraperAPI - Fazendo requisição (tentativa {attempt + 1}): {url}")
                
                if attempt > 0:
                    delay = 2 ** attempt
                    logger.info(f"Aguardando {delay}s antes da próxima tentativa...")
                    time.sleep(delay)
                
                # Parâmetros para ScraperAPI
                params = {
                    'api_key': SCRAPER_API_KEY,
                    'url': url,
                    'render': False,  # Usar render=True apenas se necessário (consome mais créditos)
                    'country_code': 'br',  # Simular requests do Brasil
                    'premium': False,  # Usar premium=True para sites mais difíceis
                    'session_number': 1,  # Manter sessão para requests relacionadas
                }
                
                # Para sites específicos, usar parâmetros otimizados
                site_type = self._identify_site(url)
                if site_type == 'amazon':
                    params['premium'] = True  # Amazon precisa de premium
                    params['render'] = True   # JavaScript pode ser necessário
                elif site_type == 'shopee':
                    params['render'] = True   # Shopee usa muito JavaScript
                elif site_type == 'mercadolivre':
                    params['session_number'] = 123  # ML pode precisar de sessão específica
                
                logger.info(f"ScraperAPI - Parâmetros: {params}")
                
                response = requests.get(
                    SCRAPER_API_URL,
                    params=params,
                    timeout=60  # ScraperAPI pode demorar mais
                )
                
                response.raise_for_status()
                
                content_length = len(response.content)
                logger.info(f"ScraperAPI - Resposta recebida: {content_length} bytes, status: {response.status_code}")
                
                if content_length < 1000:
                    logger.warning(f"ScraperAPI - Conteúdo muito pequeno: {content_length} bytes")
                    continue
                
                content_lower = response.text.lower()
                
                # Verificar se a resposta é válida
                success_indicators = [
                    '<html', '<div', 'price', 'produto', 'title'
                ]
                
                has_valid_content = any(indicator in content_lower for indicator in success_indicators)
                
                if not has_valid_content:
                    logger.warning(f"ScraperAPI - Conteúdo não parece válido na tentativa {attempt + 1}")
                    continue
                
                # Verificar se não é página de erro do ScraperAPI
                scraper_errors = [
                    'scraper api error', 'invalid api key', 'quota exceeded',
                    'blocked by target site', 'request failed'
                ]
                
                has_scraper_error = any(error in content_lower for error in scraper_errors)
                
                if has_scraper_error:
                    logger.warning(f"ScraperAPI - Erro detectado na tentativa {attempt + 1}")
                    continue
                
                logger.info(f"ScraperAPI - Requisição bem-sucedida: {content_length} bytes")
                return response.text
                
            except requests.exceptions.Timeout:
                logger.warning(f"ScraperAPI - Timeout na tentativa {attempt + 1}")
            except requests.exceptions.RequestException as e:
                logger.warning(f"ScraperAPI - Erro de requisição na tentativa {attempt + 1}: {e}")
            except Exception as e:
                logger.error(f"ScraperAPI - Erro inesperado na tentativa {attempt + 1}: {e}")
        
        logger.error(f"ScraperAPI - Falha em todas as {max_retries} tentativas para {url}")
        return None

    def _resolve_short_url(self, url: str) -> str:
        """Resolve URLs encurtadas usando ScraperAPI"""
        try:
            logger.info(f"Resolvendo URL encurtada com ScraperAPI: {url}")
            
            params = {
                'api_key': SCRAPER_API_KEY,
                'url': url,
                'render': False,
                'country_code': 'br',
                'follow_redirect': True  # ScraperAPI segue redirects automaticamente
            }
            
            response = requests.head(
                SCRAPER_API_URL,
                params=params,
                timeout=30,
                allow_redirects=True
            )
            
            # O ScraperAPI retorna a URL final no header ou podemos extrair do response
            final_url = response.url if hasattr(response, 'url') else url
            
            logger.info(f"URL resolvida com ScraperAPI: {final_url}")
            return final_url
            
        except Exception as e:
            logger.warning(f"Erro ao resolver URL com ScraperAPI: {e}")
            return url

    def _identify_site(self, url: str) -> str:
        """Identifica o site pela URL com resolução automática"""
        if any(domain in url for domain in ['amzn.to', 's.shopee.com.br', 'magazineluiza.onelink.me', 'onelink.me']):
            url = self._resolve_short_url(url)
        
        domain = urlparse(url).netloc.lower()
        
        if any(d in domain for d in ['mercadolivre.com', 'mercadolivre.com.br', 'ml.com.br', 'ml.com']):
            return 'mercadolivre'
        elif any(d in domain for d in ['amazon.com.br', 'amzn.to']):
            return 'amazon'
        elif any(d in domain for d in ['magazineluiza.com.br', 'magazinevoce.com.br', 'magazineluiza.onelink.me']):
            return 'magazineluiza'
        elif any(d in domain for d in ['shopee.com.br', 's.shopee.com.br']):
            return 'shopee'
        
        return 'unknown'

    def _clean_price(self, text: str) -> Tuple[Optional[str], Optional[float]]:
        """Limpa e formata preço com regex robusto - VERSÃO MELHORADA"""
        if not text:
            return None, None
        
        original = text.strip()
        logger.info(f"Processando preço original: '{original}'")
        
        # Remover texto extra e manter apenas números, vírgulas e pontos
        clean = re.sub(r'[^\d.,]', '', text)
        
        if not clean:
            return original, None
        
        try:
            # Lógica melhorada para processar preços brasileiros
            if ',' in clean and '.' in clean:
                # Determinar qual é o separador decimal baseado na posição
                last_comma = clean.rfind(',')
                last_dot = clean.rfind('.')
                
                if last_comma > last_dot:
                    # Formato brasileiro: 1.234.567,89
                    clean = clean.replace('.', '').replace(',', '.')
                else:
                    # Formato americano: 1,234,567.89
                    clean = clean.replace(',', '')
            elif ',' in clean:
                # Verificar se é separador decimal ou de milhares
                parts = clean.split(',')
                if len(parts) == 2 and len(parts[1]) <= 2:
                    # É separador decimal: 1234,56
                    clean = clean.replace(',', '.')
                else:
                    # É separador de milhares: 1,234 ou 1,234,567
                    clean = clean.replace(',', '')
            
            # Converter para float
            price_float = float(clean)
            
            # Formatar no padrão brasileiro correto
            if price_float >= 1000:
                # Para valores >= 1000: R$ 1.234,56
                formatted = f"R$ {price_float:,.2f}".replace(',', 'TEMP').replace('.', ',').replace('TEMP', '.')
            else:
                # Para valores < 1000: R$ 123,45
                formatted = f"R$ {price_float:.2f}".replace('.', ',')
            
            logger.info(f"Preço processado: {original} -> {formatted} (float: {price_float})")
            return formatted, price_float
            
        except ValueError:
            logger.warning(f"Erro ao converter preço: {clean}")
            return original, None

    def _calculate_discount(self, price_original: float, price_current: float) -> Optional[float]:
        """Calcula desconto percentual automaticamente"""
        if not price_original or not price_current or price_original <= price_current:
            return None
        
        discount = ((price_original - price_current) / price_original) * 100
        return round(discount)

    def _extract_mercadolivre_detailed(self, soup: BeautifulSoup) -> Dict:
        """Extrai dados do Mercado Livre - VERSÃO CORRIGIDA PARA PREÇOS"""
        data = {}
        
        try:
            logger.info("Iniciando extração detalhada do Mercado Livre")
            
            # TÍTULO
            title_selectors = [
                'h1.ui-pdp-title',
                '.ui-pdp-title',
                '.ui-pdp-header__title-container h1',
                'meta[property="og:title"]'
            ]
            
            for selector in title_selectors:
                try:
                    if selector.startswith('meta'):
                        elem = soup.find('meta', property='og:title')
                        if elem and elem.get('content'):
                            data['title'] = elem.get('content').strip()
                            logger.info(f"ML - Título encontrado via meta tag: {data['title'][:50]}...")
                            break
                    else:
                        elem = soup.select_one(selector)
                        if elem:
                            title_text = elem.get_text(strip=True)
                            if title_text and len(title_text) > 3:
                                data['title'] = title_text
                                logger.info(f"ML - Título encontrado: {title_text[:50]}...")
                                break
                except Exception as e:
                    logger.debug(f"Erro no seletor de título '{selector}': {e}")
                    continue
            
            # PREÇOS - LÓGICA COMPLETAMENTE REESCRITA
            logger.info("Extraindo preços do Mercado Livre com nova lógica")
            
            # Método 1: Buscar preços via meta tags (mais confiável)
            try:
                price_meta = soup.find('meta', property='product:price:amount')
                if price_meta and price_meta.get('content'):
                    price_value = price_meta.get('content')
                    # Formatar preço
                    try:
                        price_float = float(price_value)
                        if price_float >= 1000:
                            formatted_price = f"R$ {price_float:,.2f}".replace(',', 'TEMP').replace('.', ',').replace('TEMP', '.')
                        else:
                            formatted_price = f"R$ {price_float:.2f}".replace('.', ',')
                        data['price_current_text'] = formatted_price
                        logger.info(f"ML - Preço via meta tag: {formatted_price}")
                    except ValueError:
                        data['price_current_text'] = f"R$ {price_value}"
                        logger.info(f"ML - Preço via meta tag (raw): R$ {price_value}")
            except Exception as e:
                logger.debug(f"Erro na extração via meta tag: {e}")
            
            # Método 2: Buscar por classes específicas do ML se meta tag falhar
            if not data.get('price_current_text'):
                price_selectors = [
                    '.andes-money-amount__fraction',
                    '.price-tag-fraction',
                    '.ui-pdp-price__fraction',
                    '.andes-money-amount__currency-symbol + .andes-money-amount__fraction'
                ]
                
                for selector in price_selectors:
                    try:
                        # Buscar todos os elementos de preço
                        price_elements = soup.select(selector)
                        
                        for elem in price_elements:
                            # Verificar se não está dentro de <s> (preço riscado)
                            if not elem.find_parent('s'):
                                fraction_text = elem.get_text(strip=True)
                                
                                if fraction_text and fraction_text.replace('.', '').replace(',', '').isdigit():
                                    # Buscar centavos no mesmo container
                                    parent = elem.find_parent('span', class_='andes-money-amount')
                                    if parent:
                                        cents_elem = parent.find('span', class_='andes-money-amount__cents')
                                        if cents_elem:
                                            cents = cents_elem.get_text(strip=True)
                                            if cents:
                                                fraction_text += f",{cents}"
                                        else:
                                            # Se não há centavos explícitos, adicionar ,00
                                            if ',' not in fraction_text:
                                                fraction_text += ",00"
                                    
                                    data['price_current_text'] = f"R$ {fraction_text}"
                                    logger.info(f"ML - Preço atual encontrado: R$ {fraction_text}")
                                    break
                        
                        if data.get('price_current_text'):
                            break
                            
                    except Exception as e:
                        logger.debug(f"Erro no seletor de preço '{selector}': {e}")
                        continue
            
            # Método 3: Buscar preço riscado (original)
            try:
                # Buscar especificamente dentro de <s> para preço original
                original_selectors = [
                    's .andes-money-amount__fraction',
                    's .price-tag-fraction',
                    '.price-tag-symbol + s .price-tag-fraction'
                ]
                
                for selector in original_selectors:
                    original_elem = soup.select_one(selector)
                    if original_elem:
                        fraction_text = original_elem.get_text(strip=True)
                        
                        if fraction_text:
                            # Buscar centavos no container <s>
                            s_container = original_elem.find_parent('s')
                            if s_container:
                                cents_elem = s_container.find('span', class_='andes-money-amount__cents')
                                if cents_elem:
                                    cents = cents_elem.get_text(strip=True)
                                    if cents:
                                        fraction_text += f",{cents}"
                                elif ',' not in fraction_text:
                                    fraction_text += ",00"
                            
                            data['price_original_text'] = f"R$ {fraction_text}"
                            logger.info(f"ML - Preço original encontrado: R$ {fraction_text}")
                            break
                            
            except Exception as e:
                logger.debug(f"Erro ao extrair preço original: {e}")
            
            # Método 4: Fallback usando regex no HTML bruto
            if not data.get('price_current_text'):
                try:
                    html_str = str(soup)
                    # Buscar padrões de preço no HTML
                    price_patterns = [
                        r'"amount":(\d+\.?\d*)',
                        r'"price":(\d+\.?\d*)',
                        r'R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2})',
                        r'(\d{1,3}(?:\.\d{3})*,\d{2})'
                    ]
                    
                    for pattern in price_patterns:
                        matches = re.findall(pattern, html_str)
                        for match in matches:
                            try:
                                # Tentar converter para float para validar
                                if ',' in match:
                                    test_price = float(match.replace('.', '').replace(',', '.'))
                                else:
                                    test_price = float(match)
                                
                                # Verificar se é um preço razoável (entre R$ 1 e R$ 100.000)
                                if 1 <= test_price <= 100000:
                                    if test_price >= 1000:
                                        formatted = f"R$ {test_price:,.2f}".replace(',', 'TEMP').replace('.', ',').replace('TEMP', '.')
                                    else:
                                        formatted = f"R$ {test_price:.2f}".replace('.', ',')
                                    
                                    data['price_current_text'] = formatted
                                    logger.info(f"ML - Preço via regex: {formatted}")
                                    break
                            except ValueError:
                                continue
                        
                        if data.get('price_current_text'):
                            break
                            
                except Exception as e:
                    logger.debug(f"Erro na extração via regex: {e}")
            
            # AVALIAÇÃO
            try:
                rating_elem = soup.select_one('.ui-pdp-review__rating')
                if rating_elem:
                    rating_text = rating_elem.get_text(strip=True)
                    try:
                        data['rating'] = float(rating_text.replace(',', '.'))
                        logger.info(f"ML - Avaliação encontrada: {data['rating']}")
                    except ValueError:
                        logger.debug(f"Erro ao converter avaliação: {rating_text}")
                
                rating_count_elem = soup.select_one('.ui-pdp-review__amount')
                if rating_count_elem:
                    count_text = rating_count_elem.get_text(strip=True)
                    count_match = re.search(r'\((\d+)\)', count_text)
                    if count_match:
                        data['rating_count'] = int(count_match.group(1))
                        logger.info(f"ML - Quantidade de avaliações: {data['rating_count']}")
            except Exception as e:
                logger.debug(f"Erro ao extrair avaliação: {e}")
            
            # IMAGEM
            img_selectors = [
                'figure.ui-pdp-gallery__figure img[data-zoom]',
                'figure.ui-pdp-gallery__figure img',
                '.ui-pdp-gallery__figure img',
                'meta[property="og:image"]'
            ]
            
            for selector in img_selectors:
                try:
                    if selector.startswith('meta'):
                        elem = soup.find('meta', property='og:image')
                        if elem and elem.get('content'):
                            data['image_url'] = elem.get('content')
                            logger.info(f"ML - Imagem via meta tag: {data['image_url'][:50]}...")
                            break
                    else:
                        elem = soup.select_one(selector)
                        if elem:
                            img_url = (elem.get('data-zoom') or 
                                     elem.get('src') or 
                                     elem.get('data-src'))
                            if img_url and 'http' in img_url:
                                data['image_url'] = img_url
                                logger.info(f"ML - Imagem encontrada: {img_url[:50]}...")
                                break
                except Exception as e:
                    logger.debug(f"Erro no seletor de imagem '{selector}': {e}")
                    continue
            
            # INFORMAÇÕES EXTRAS
            try:
                subtitle_elem = soup.select_one('.ui-pdp-subtitle')
                if subtitle_elem:
                    subtitle_text = subtitle_elem.get_text(strip=True)
                    if '|' in subtitle_text:
                        parts = subtitle_text.split('|')
                        data['condition'] = parts[0].strip()
                        data['sold_quantity'] = parts[1].strip() if len(parts) > 1 else None
                        logger.info(f"ML - Condição: {data['condition']}, Vendidos: {data['sold_quantity']}")
                
                shipping_elem = soup.select_one('.ui-pdp-promotions-pill-label')
                if shipping_elem:
                    shipping_text = shipping_elem.get_text(strip=True)
                    data['shipping_info'] = shipping_text
                    data['free_shipping'] = 'GRÁTIS' in shipping_text.upper()
                    logger.info(f"ML - Frete: {shipping_text}")
                    
            except Exception as e:
                logger.debug(f"Erro ao extrair informações extras: {e}")
            
        except Exception as e:
            logger.error(f"Erro geral na extração do Mercado Livre: {e}")
        
        logger.info(f"ML - Extração concluída. Campos encontrados: {list(data.keys())}")
        return data

    def _extract_amazon_detailed(self, soup: BeautifulSoup) -> Dict:
        """Extrai dados da Amazon - VERSÃO MELHORADA"""
        data = {}
        
        try:
            logger.info("Iniciando extração detalhada da Amazon")
            
            page_text = soup.get_text().lower()
            blocked_indicators = [
                'dogs of amazon', 'sorry, we just need to make sure',
                'enter the characters you see', 'robot', 'captcha'
            ]
            
            is_blocked = any(indicator in page_text for indicator in blocked_indicators)
            
            if is_blocked:
                logger.warning("Amazon: Página com bloqueio detectado, usando extração limitada")
                return self._extract_amazon_blocked(str(soup))
            
            # TÍTULO
            title_selectors = [
                '#productTitle',
                'span#productTitle',
                '.product-title-word-break',
                'meta[name="title"]'
            ]
            
            for selector in title_selectors:
                try:
                    if selector.startswith('meta'):
                        elem = soup.find('meta', attrs={'name': 'title'})
                        if elem and elem.get('content'):
                            data['title'] = elem.get('content').strip()
                            logger.info(f"Amazon - Título via meta: {data['title'][:50]}...")
                            break
                    else:
                        elem = soup.select_one(selector)
                        if elem:
                            title_text = elem.get_text(strip=True)
                            if title_text and len(title_text) > 5:
                                data['title'] = title_text
                                logger.info(f"Amazon - Título encontrado: {title_text[:50]}...")
                                break
                except Exception as e:
                    logger.debug(f"Erro no seletor de título '{selector}': {e}")
                    continue
            
            # PREÇO ATUAL - LÓGICA MELHORADA
            logger.info("Extraindo preços da Amazon")
            
            # Método 1: .priceToPay
            try:
                price_container = soup.select_one('.priceToPay')
                if price_container:
                    whole_elem = price_container.select_one('.a-price-whole')
                    if whole_elem:
                        whole_text = whole_elem.get_text(strip=True)
                        
                        fraction_elem = price_container.select_one('.a-price-fraction')
                        if fraction_elem:
                            fraction_text = fraction_elem.get_text(strip=True)
                            data['price_current_text'] = f"R$ {whole_text},{fraction_text}"
                        else:
                            data['price_current_text'] = f"R$ {whole_text}"
                        
                        logger.info(f"Amazon - Preço atual (método 1): {data['price_current_text']}")
            except Exception as e:
                logger.debug(f"Erro no método 1 de preço: {e}")
            
            # Método 2: .a-offscreen
            if not data.get('price_current_text'):
                offscreen_selectors = [
                    '.a-price.priceToPay .a-offscreen',
                    '.priceToPay .a-offscreen',
                    '.a-price-current .a-offscreen',
                    '.a-price .a-offscreen'
                ]
                
                for selector in offscreen_selectors:
                    try:
                        elem = soup.select_one(selector)
                        if elem:
                            price_text = elem.get_text(strip=True)
                            if 'R in price_text or ' in price_text:
                                data['price_current_text'] = price_text
                                logger.info(f"Amazon - Preço atual (offscreen): {price_text}")
                                break
                    except Exception as e:
                        logger.debug(f"Erro no seletor offscreen '{selector}': {e}")
                        continue
            
            # PREÇO ORIGINAL
            original_selectors = [
                '.basisPrice .a-offscreen',
                '.a-price.a-text-price .a-offscreen',
                '.a-text-strike .a-offscreen'
            ]
            
            for selector in original_selectors:
                try:
                    elem = soup.select_one(selector)
                    if elem:
                        price_text = elem.get_text(strip=True)
                        if 'R in price_text or ' in price_text:
                            data['price_original_text'] = price_text
                            logger.info(f"Amazon - Preço original: {price_text}")
                            break
                except Exception as e:
                    logger.debug(f"Erro no seletor de preço original '{selector}': {e}")
                    continue
            
            # AVALIAÇÃO
            try:
                rating_elem = soup.select_one('#acrPopover .a-icon-alt')
                if rating_elem:
                    rating_text = rating_elem.get_text()
                    rating_match = re.search(r'(\d[.,]\d)', rating_text)
                    if rating_match:
                        data['rating'] = float(rating_match.group(1).replace(',', '.'))
                        logger.info(f"Amazon - Avaliação: {data['rating']}")
                
                rating_count_elem = soup.select_one('#acrCustomerReviewText')
                if rating_count_elem:
                    count_text = rating_count_elem.get_text()
                    count_match = re.search(r'(\d+)', count_text.replace('.', '').replace(',', ''))
                    if count_match:
                        data['rating_count'] = int(count_match.group(1))
                        logger.info(f"Amazon - Quantidade de avaliações: {data['rating_count']}")
            except Exception as e:
                logger.debug(f"Erro ao extrair avaliação: {e}")
            
            # IMAGEM
            img_selectors = [
                '#landingImage',
                '#imgBlkFront',
                '.a-dynamic-image'
            ]
            
            for selector in img_selectors:
                try:
                    elem = soup.select_one(selector)
                    if elem:
                        img_url = (elem.get('data-old-hires') or 
                                 elem.get('src') or 
                                 elem.get('data-src'))
                        if img_url and ('http' in img_url or img_url.startswith('//')):
                            if img_url.startswith('//'):
                                img_url = 'https:' + img_url
                            data['image_url'] = img_url
                            logger.info(f"Amazon - Imagem encontrada: {img_url[:50]}...")
                            break
                except Exception as e:
                    logger.debug(f"Erro no seletor de imagem '{selector}': {e}")
                    continue
            
            # DESCONTO
            try:
                discount_elem = soup.select_one('.savingsPercentage')
                if discount_elem:
                    discount_text = discount_elem.get_text()
                    discount_match = re.search(r'(\d+)%', discount_text)
                    if discount_match:
                        data['discount_percentage'] = float(discount_match.group(1))
                        logger.info(f"Amazon - Desconto: {data['discount_percentage']}%")
            except Exception as e:
                logger.debug(f"Erro ao extrair desconto: {e}")
            
        except Exception as e:
            logger.error(f"Erro geral na extração da Amazon: {e}")
        
        logger.info(f"Amazon - Extração concluída. Campos encontrados: {list(data.keys())}")
        return data

    def _extract_amazon_blocked(self, html_str: str) -> Dict:
        """Extrai dados da Amazon quando bloqueada usando regex"""
        data = {}
        
        try:
            logger.info("Amazon bloqueada - usando extração via regex")
            
            title_patterns = [
                r'<title[^>]*>([^<]+)</title>',
                r'"title":"([^"]+)"',
                r'productTitle[^>]*>([^<]+)<'
            ]
            
            for pattern in title_patterns:
                match = re.search(pattern, html_str, re.IGNORECASE)
                if match:
                    title = match.group(1).strip()
                    title = re.sub(r':\s*Amazon\.com\.br.*', '', title)
                    title = re.sub(r'\s*-\s*Amazon\.com\.br.*', '', title)
                    if len(title) > 5:
                        data['title'] = title
                        logger.info(f"Amazon (bloqueada) - Título: {title[:50]}...")
                        break
            
            # Preço via regex
            price_patterns = [
                r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})',
                r'"priceAmount":"([^"]+)"',
                r'"price":"([^"]+)"'
            ]
            
            for pattern in price_patterns:
                match = re.search(pattern, html_str)
                if match:
                    price_str = match.group(1)
                    if not price_str.startswith('R'):
                        price_str = f"R$ {price_str}"
                    data['price_current_text'] = price_str
                    logger.info(f"Amazon (bloqueada) - Preço: {price_str}")
                    break
            
        except Exception as e:
            logger.error(f"Erro na extração Amazon bloqueada: {e}")
        
        return data

    def _extract_magazineluiza_detailed(self, soup: BeautifulSoup) -> Dict:
        """Extrai dados do Magazine Luiza - VERSÃO MELHORADA"""
        data = {}
        
        try:
            logger.info("Iniciando extração detalhada do Magazine Luiza")
            
            # TÍTULO
            title_selectors = [
                '[data-testid="heading-product-title"]',
                'h1[data-testid="heading-product-title"]',
                '.sc-dcJsrY.jjGTqv',
                'meta[property="og:title"]'
            ]
            
            for selector in title_selectors:
                try:
                    if selector.startswith('meta'):
                        elem = soup.find('meta', property='og:title')
                        if elem and elem.get('content'):
                            data['title'] = elem.get('content').strip()
                            logger.info(f"Magalu - Título via meta: {data['title'][:50]}...")
                            break
                    else:
                        elem = soup.select_one(selector)
                        if elem:
                            title_text = elem.get_text(strip=True)
                            if title_text and len(title_text) > 3:
                                data['title'] = title_text
                                logger.info(f"Magalu - Título encontrado: {title_text[:50]}...")
                                break
                except Exception as e:
                    logger.debug(f"Erro no seletor de título '{selector}': {e}")
                    continue
            
            # AVALIAÇÃO
            try:
                review_elem = soup.select_one('[data-testid="review"]')
                if review_elem:
                    aria_label = review_elem.get('aria-label', '')
                    rating_match = re.search(r'(\d[.,]\d)', aria_label)
                    if rating_match:
                        data['rating'] = float(rating_match.group(1).replace(',', '.'))
                        logger.info(f"Magalu - Avaliação: {data['rating']}")
                    
                    count_match = re.search(r'\((\d+)\)', aria_label)
                    if count_match:
                        data['rating_count'] = int(count_match.group(1))
                        logger.info(f"Magalu - Quantidade de avaliações: {data['rating_count']}")
            except Exception as e:
                logger.debug(f"Erro ao extrair avaliação: {e}")
            
            # PREÇOS - LÓGICA MELHORADA
            logger.info("Extraindo preços do Magazine Luiza")
            
            # Preço original (antes do desconto)
            try:
                price_original_elem = soup.select_one('[data-testid="price-original"]')
                if price_original_elem:
                    data['price_original_text'] = price_original_elem.get_text(strip=True)
                    logger.info(f"Magalu - Preço original: {data['price_original_text']}")
            except Exception as e:
                logger.debug(f"Erro ao extrair preço original: {e}")
            
            # Preço atual (após desconto)
            try:
                price_current_elem = soup.select_one('[data-testid="price-value"]')
                if price_current_elem:
                    data['price_current_text'] = price_current_elem.get_text(strip=True)
                    logger.info(f"Magalu - Preço atual: {data['price_current_text']}")
            except Exception as e:
                logger.debug(f"Erro ao extrair preço atual: {e}")
            
            # IMAGEM
            try:
                img_elem = soup.select_one('[data-testid="image-selected-thumbnail"]')
                if img_elem:
                    img_url = img_elem.get('src')
                    if img_url:
                        data['image_url'] = img_url
                        logger.info(f"Magalu - Imagem encontrada: {img_url[:50]}...")
            except Exception as e:
                logger.debug(f"Erro ao extrair imagem: {e}")
            
            # INFORMAÇÕES EXTRAS
            try:
                installment_elem = soup.select_one('[data-testid="installment"]')
                if installment_elem:
                    installment_info = installment_elem.get_text(strip=True)
                    logger.info(f"Magalu - Parcelamento: {installment_info}")
            except Exception as e:
                logger.debug(f"Erro ao extrair parcelamento: {e}")
            
        except Exception as e:
            logger.error(f"Erro geral na extração do Magazine Luiza: {e}")
        
        logger.info(f"Magalu - Extração concluída. Campos encontrados: {list(data.keys())}")
        return data

    def _extract_shopee_detailed(self, soup: BeautifulSoup) -> Dict:
        """Extrai dados do Shopee - VERSÃO COMPLETAMENTE NOVA E MELHORADA"""
        data = {}
        
        try:
            logger.info("Iniciando extração detalhada do Shopee (NOVA VERSÃO)")
            html_str = str(soup)
            
            # MÉTODO 1: Tentar seletores CSS primeiro
            logger.info("Shopee - Tentando seletores CSS...")
            
            # TÍTULO via seletores CSS
            title_selectors = [
                'h1[data-testid="pdp-product-title"]',
                '.shopee-page-product-detail__title',
                '[data-testid="pdp-product-title"]',
                '.shopee-product-title',
                'h1',
                'meta[property="og:title"]'
            ]
            
            for selector in title_selectors:
                try:
                    if selector.startswith('meta'):
                        elem = soup.find('meta', property='og:title')
                        if elem and elem.get('content'):
                            title = elem.get('content').strip()
                            if 'Shopee' not in title and len(title) > 5:
                                data['title'] = title
                                logger.info(f"Shopee - Título via meta: {title[:50]}...")
                                break
                    else:
                        elem = soup.select_one(selector)
                        if elem:
                            title_text = elem.get_text(strip=True)
                            if title_text and len(title_text) > 5 and 'Shopee' not in title_text:
                                data['title'] = title_text
                                logger.info(f"Shopee - Título encontrado: {title_text[:50]}...")
                                break
                except Exception as e:
                    logger.debug(f"Erro no seletor de título '{selector}': {e}")
                    continue
            
            # PREÇO via seletores CSS
            price_selectors = [
                '[data-testid="pdp-price"]',
                '.shopee-product-price',
                '.product-price',
                '.price-current',
                '[class*="price"]'
            ]
            
            for selector in price_selectors:
                try:
                    elem = soup.select_one(selector)
                    if elem:
                        price_text = elem.get_text(strip=True)
                        if 'R' in price_text:
                            data['price_current_text'] = price_text
                            logger.info(f"Shopee - Preço via CSS: {price_text}")
                            break
                except Exception as e:
                    logger.debug(f"Erro no seletor de preço '{selector}': {e}")
                    continue
            
            # IMAGEM via seletores CSS
            img_selectors = [
                '[data-testid="pdp-main-image"]',
                '.shopee-image-full img',
                '.product-image img',
                '.main-image img'
            ]
            
            for selector in img_selectors:
                try:
                    elem = soup.select_one(selector)
                    if elem:
                        img_url = elem.get('src') or elem.get('data-src')
                        if img_url and ('http' in img_url or img_url.startswith('//')):
                            if img_url.startswith('//'):
                                img_url = 'https:' + img_url
                            data['image_url'] = img_url
                            logger.info(f"Shopee - Imagem via CSS: {img_url[:50]}...")
                            break
                except Exception as e:
                    logger.debug(f"Erro no seletor de imagem '{selector}': {e}")
                    continue
            
            # MÉTODO 2: Se CSS falhou, usar REGEX melhorado
            if not data.get('title') or not data.get('price_current_text'):
                logger.info("Shopee - Seletores CSS falharam, usando REGEX melhorado...")
                
                # TÍTULO via regex - padrões melhorados
                if not data.get('title'):
                    title_patterns = [
                        r'"pdpTitle":"([^"]+)"',
                        r'"title":"([^"]+)"',
                        r'"productName":"([^"]+)"',
                        r'"name":"([^"]+)"',
                        r'<title[^>]*>([^<]*[Cc]alça[^<]*)</title>',
                        r'<h1[^>]*>([^<]+)</h1>'
                    ]
                    
                    for pattern in title_patterns:
                        try:
                            match = re.search(pattern, html_str, re.IGNORECASE)
                            if match:
                                title = match.group(1).strip()
                                # Decodificar unicode se necessário
                                title = title.encode().decode('unicode_escape')
                                
                                if ('Shopee' not in title and 
                                    len(title) > 5 and 
                                    not title.startswith('{') and
                                    'login' not in title.lower()):
                                    data['title'] = title
                                    logger.info(f"Shopee - Título via regex: {title[:50]}...")
                                    break
                        except Exception as e:
                            logger.debug(f"Erro no padrão de título '{pattern}': {e}")
                            continue
                
                # PREÇO via regex - padrões melhorados
                if not data.get('price_current_text'):
                    price_patterns = [
                        r'"price":(\d+)',
                        r'"minPrice":(\d+)', 
                        r'"maxPrice":(\d+)',
                        r'"priceMin":(\d+)',
                        r'"priceMax":(\d+)',
                        r'R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2})',
                        r'"currentPrice":"([^"]+)"'
                    ]
                    
                    for pattern in price_patterns:
                        try:
                            match = re.search(pattern, html_str)
                            if match:
                                price_str = match.group(1)
                                
                                if price_str.isdigit():
                                    # Converter de centavos para reais (formato Shopee)
                                    price_float = float(price_str) / 100000
                                    if price_float > 0.01:  # Preço válido
                                        formatted_price = f"R$ {price_float:,.2f}".replace(',', 'TEMP').replace('.', ',').replace('TEMP', '.')
                                        data['price_current_text'] = formatted_price
                                        logger.info(f"Shopee - Preço via regex: {formatted_price}")
                                        break
                                elif 'R' in price_str:
                                    data['price_current_text'] = price_str
                                    logger.info(f"Shopee - Preço via regex: {price_str}")
                                    break
                        except Exception as e:
                            logger.debug(f"Erro no padrão de preço '{pattern}': {e}")
                            continue
                
                # IMAGEM via regex
                if not data.get('image_url'):
                    img_patterns = [
                        r'"image":"([^"]+)"',
                        r'"images":\["([^"]+)"',
                        r'"mainImage":"([^"]+)"',
                        r'<img[^>]+src="([^"]+shopee[^"]*)"'
                    ]
                    
                    for pattern in img_patterns:
                        try:
                            match = re.search(pattern, html_str)
                            if match:
                                img_url = match.group(1)
                                if 'http' in img_url or img_url.startswith('//'):
                                    if img_url.startswith('//'):
                                        img_url = 'https:' + img_url
                                    data['image_url'] = img_url
                                    logger.info(f"Shopee - Imagem via regex: {img_url[:50]}...")
                                    break
                        except Exception as e:
                            logger.debug(f"Erro no padrão de imagem '{pattern}': {e}")
                            continue
            
            # AVALIAÇÃO via regex
            try:
                rating_patterns = [
                    r'"rating":(\d[.,]\d)',
                    r'"averageRating":(\d[.,]\d)',
                    r'"ratingScore":(\d[.,]\d)'
                ]
                
                for pattern in rating_patterns:
                    match = re.search(pattern, html_str)
                    if match:
                        rating_str = match.group(1)
                        data['rating'] = float(rating_str.replace(',', '.'))
                        logger.info(f"Shopee - Avaliação: {data['rating']}")
                        break
                
                # Quantidade de avaliações
                count_patterns = [
                    r'"ratingCount":(\d+)',
                    r'"reviewCount":(\d+)',
                    r'"totalReviews":(\d+)'
                ]
                
                for pattern in count_patterns:
                    match = re.search(pattern, html_str)
                    if match:
                        data['rating_count'] = int(match.group(1))
                        logger.info(f"Shopee - Quantidade de avaliações: {data['rating_count']}")
                        break
                        
            except Exception as e:
                logger.debug(f"Erro ao extrair avaliação: {e}")
            
        except Exception as e:
            logger.error(f"Erro geral na extração do Shopee: {e}")
        
        logger.info(f"Shopee - Extração concluída. Campos encontrados: {list(data.keys())}")
        return data

    def scrape_product(self, url: str) -> ProductData:
        """Método principal de extração com ScraperAPI - VERSÃO MELHORADA"""
        start_time = time.time()
        product = ProductData(url=url, errors=[])
        
        try:
            logger.info(f"=== INICIANDO EXTRAÇÃO COM SCRAPERAPI ===")
            logger.info(f"URL original: {url}")
            
            # Identificar site e resolver URLs encurtadas
            site_type = self._identify_site(url)
            
            site_names = {
                'mercadolivre': 'Mercado Livre',
                'amazon': 'Amazon Brasil',
                'magazineluiza': 'Magazine Luiza',
                'shopee': 'Shopee Brasil'
            }
            
            product.site_name = site_names.get(site_type, 'Site desconhecido')
            
            if site_type == 'unknown':
                product.errors.append("Site não suportado")
                logger.error(f"Site não suportado: {url}")
                return product
            
            logger.info(f"Site identificado: {product.site_name}")
            
            # Resolver URL final se necessário
            final_url = url
            if any(domain in url for domain in ['amzn.to', 's.shopee.com.br', 'magazineluiza.onelink.me', 'onelink.me']):
                final_url = self._resolve_short_url(url)
                logger.info(f"URL final após resolução: {final_url}")
            
            # Fazer requisição usando ScraperAPI
            html = self._make_scraperapi_request(final_url)
            if not html:
                product.errors.append("Não foi possível acessar a página via ScraperAPI")
                logger.error("Falha ao obter HTML da página via ScraperAPI")
                return product
            
            logger.info(f"HTML obtido via ScraperAPI com sucesso: {len(html)} caracteres")
            
            # Parsear HTML
            soup = BeautifulSoup(html, 'html.parser')
            
            # Extrair dados baseado no site identificado
            if site_type == 'mercadolivre':
                extracted = self._extract_mercadolivre_detailed(soup)
            elif site_type == 'amazon':
                extracted = self._extract_amazon_detailed(soup)
            elif site_type == 'magazineluiza':
                extracted = self._extract_magazineluiza_detailed(soup)
            elif site_type == 'shopee':
                extracted = self._extract_shopee_detailed(soup)
            else:
                extracted = {}
            
            # Aplicar dados extraídos ao produto
            for key, value in extracted.items():
                if value:  # Só aplicar se o valor não for None ou vazio
                    setattr(product, key, value)
            
            # Processar e formatar preços - VERSÃO MELHORADA
            if product.price_current_text:
                formatted, numeric = self._clean_price(product.price_current_text)
                if numeric:
                    product.price_current = numeric
                    product.price_current_text = formatted
                    logger.info(f"Preço atual processado: {formatted} (valor: {numeric})")
            
            if product.price_original_text:
                formatted, numeric = self._clean_price(product.price_original_text)
                if numeric:
                    product.price_original = numeric
                    product.price_original_text = formatted
                    logger.info(f"Preço original processado: {formatted} (valor: {numeric})")
            
            # Calcular desconto automaticamente - NOVA FUNCIONALIDADE
            if product.price_current and product.price_original:
                calculated_discount = self._calculate_discount(product.price_original, product.price_current)
                if calculated_discount:
                    product.discount_percentage = calculated_discount
                    logger.info(f"Desconto calculado automaticamente: {calculated_discount}%")
            elif not product.discount_percentage and extracted.get('discount_percentage'):
                # Usar desconto extraído do site se não conseguiu calcular
                product.discount_percentage = extracted['discount_percentage']
                logger.info(f"Desconto obtido do site: {product.discount_percentage}%")
            
            # Validações finais
            if not product.title:
                product.errors.append("Título não encontrado")
            
            if not product.price_current:
                product.errors.append("Preço não encontrado")
            
            # Log final
            success_fields = [k for k, v in extracted.items() if v]
            logger.info(f"=== EXTRAÇÃO CONCLUÍDA COM SCRAPERAPI ===")
            logger.info(f"Site: {product.site_name}")
            logger.info(f"Campos extraídos: {success_fields}")
            logger.info(f"Erros: {len(product.errors)}")
            
        except Exception as e:
            error_msg = f"Erro na extração com ScraperAPI: {str(e)}"
            product.errors.append(error_msg)
            logger.error(error_msg, exc_info=True)
        
        product.extraction_time = time.time() - start_time
        logger.info(f"Tempo total de extração: {product.extraction_time:.2f}s")
        
        return product

# Instância global do scraper
scraper = ProScraper()

@app.route('/')
def index():
    """Serve o HTML da aplicação com fallback"""
    try:
        # Tentar renderizar o template
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Erro ao renderizar template: {e}")
        # Fallback: retornar HTML inline
        return """