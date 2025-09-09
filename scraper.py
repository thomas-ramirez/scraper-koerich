import os, re, json, time
import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
from pathlib import Path
from requests.adapters import HTTPAdapter, Retry
from contextlib import contextmanager

try:
    # Playwright é opcional; usado para páginas dinâmicas (ex.: Colcci)
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

downloads_path = str(Path.home() / "Downloads")
current_dir = os.path.dirname(os.path.abspath(__file__))  # Pasta atual do script

input_csv = os.path.join(current_dir, "data", "csv", "produtos_link.csv")
output_csv = os.path.join(current_dir, "data", "exports", "produtos_vtex.csv")
output_folder = os.path.join(current_dir, "data", "exports", "imagens_produtos")

df_links = pd.read_csv(input_csv)
if "url" not in df_links.columns:
    raise Exception("❌ A planilha precisa ter uma coluna chamada 'url'.")

os.makedirs(output_folder, exist_ok=True)

# === Sessão HTTP robusta ===
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}
session = requests.Session()
session.headers.update(UA)
session.mount(
    "https://",
    HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])),
)

# === Mapeamentos VTEX (IDs) ===
maps = {
    "departamento": {
        "Eletrodomésticos": "1",
        "Eletroportáteis": "2", 
        "Ar Condicionado": "3",
        "Aquecimento": "4",
        "Ventilação": "5",
        "Refrigeração": "6",
        "Lavagem": "7",
        "Cozinha": "8",
        "Limpeza": "9",
        "Pequenos Eletrodomésticos": "10",
        # Adicione mais conforme necessário
    },
    "categoria": {
        "Frigobar": "1",
        "Freezer": "2",
        "Refrigerador": "3",
        "Ar Condicionado": "4",
        "Ventilador": "5",
        "Aquecedor": "6",
        "Máquina de Lavar": "7",
        "Secadora": "8",
        "Fogão": "9",
        "Microondas": "10",
        "Liquidificador": "11",
        "Mixer": "12",
        "Processador": "13",
        "Aspirador": "14",
        "Ferro de Passar": "15",
        # Adicione mais conforme necessário
    }
}

# Mapeamento específico de marcas para Koerich
marca_mapping = {
    "BRASTEMP": "2000009",
    "ELECTROLUX": "2000010", 
    "MONDIAL": "2000011"
}

# === Utils ===
def limpar(t):
    return re.sub(r"\s+", " ", (t or "").strip())

def get_marca_id(marca_nome):
    """Retorna o ID da marca específico para Koerich"""
    if not marca_nome:
        return "2000009"  # Default Brastemp
    
    marca_upper = marca_nome.upper().strip()
    return marca_mapping.get(marca_upper, "2000009")  # Default Brastemp

def parse_preco(texto):
    # 1ª ocorrência de R$ xxx,xx
    m = re.search(r"R\$\s*([\d\.\s]+,\d{2})", texto)
    if not m:
        return ""
    br = m.group(1).replace(".", "").replace(" ", "").replace(",", ".")
    try:
        return f"{float(br):.2f}"
    except:
        return ""

def get_next_data(soup):
    tag = soup.find("script", id="__NEXT_DATA__", type="application/json")
    if not tag:
        return None
    try:
        return json.loads(tag.string)
    except:
        return None

def get_jsonld(soup):
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string)
            seq = data if isinstance(data, list) else [data]
            for it in seq:
                if isinstance(it, dict) and it.get("@type") in ("Product", "Offer", "AggregateOffer"):
                    return it
        except:
            continue
    return None

def parse_srcset(srcset):
    # retorna a URL com maior resolução do srcset
    # ex: "https://a.jpg 1x, https://b.jpg 2x" -> "https://b.jpg"
    if not srcset:
        return ""
    
    parts = srcset.split(",")
    best_url = ""
    best_density = 0
    
    for part in parts:
        part = part.strip()
        if ' ' in part:
            url_part, density_part = part.rsplit(' ', 1)
            try:
                # Remove 'x' e converte para float
                density = float(density_part.replace('x', '').replace('w', ''))
                if density > best_density:
                    best_density = density
                    best_url = url_part.strip()
            except:
                # Se não conseguir parsear, usa a primeira URL
                if not best_url:
                    best_url = url_part.strip()
    
    return best_url if best_url else parts[0].strip().split(" ")[0].strip()

@contextmanager
def _playwright_context():
    if not sync_playwright:
        raise RuntimeError("Playwright não está disponível. Instale com: pip install playwright e python -m playwright install chromium")
    p = sync_playwright().start()
    try:
        yield p
    finally:
        p.stop()

def renderizar_html(url, wait_selectors=None, timeout_ms=15000):
    """Renderiza página via Chromium headless e retorna HTML.
    wait_selectors: lista de seletores CSS a esperar (qualquer um).
    """
    wait_selectors = wait_selectors or []
    with _playwright_context() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="domcontentloaded")
            # tentar aguardar rede ociosa e algum seletor de conteúdo
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass
            for sel in wait_selectors:
                try:
                    page.wait_for_selector(sel, state="attached", timeout=4000)
                    break
                except Exception:
                    continue
            return page.content()
        finally:
            browser.close()

def gerar_base_url_produto(sku, nome):
    """Gera baseUrl único para cada produto baseado no SKU e nome"""
    # Limpar nome para URL segura
    nome_limpo = re.sub(r'[^a-zA-Z0-9\s-]', '', nome).strip()
    nome_limpo = re.sub(r'\s+', '-', nome_limpo).lower()
    
    # Gerar baseUrl no formato: images-{leadPOC}-{sku}-{nome}
    base_url = f"images-leadPOC-{sku}-{nome_limpo}"
    return base_url

def baixar_imagem(url_img, fname):
    try:
        # Adicionar headers para tentar obter imagem em melhor qualidade
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
            'Accept-Encoding': 'identity',
            'Referer': 'https://www.koerich.com.br/',
        }
        
        with session.get(url_img, headers=headers, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            
            # Verificar se é realmente uma imagem
            content_type = resp.headers.get('content-type', '')
            if not content_type.startswith('image/'):
                print(f"⚠️ URL não é uma imagem: {content_type}")
                return False
            
            # Verificar tamanho da imagem
            content_length = resp.headers.get('content-length')
            if content_length:
                size_kb = int(content_length) / 1024
                if size_kb < 1:  # Menos de 1KB, provavelmente thumbnail
                    print(f"⚠️ Imagem muito pequena ({size_kb:.1f}KB), pode ser thumbnail")
            
            with open(os.path.join(output_folder, fname), "wb") as f:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        f.write(chunk)
        
        # Verificar se o arquivo foi criado e tem tamanho mínimo
        file_path = os.path.join(output_folder, fname)
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            if file_size < 1024:  # Menos de 1KB
                print(f"⚠️ Arquivo muito pequeno ({file_size} bytes): {fname}")
                return False
        
        return True
    except Exception as e:
        print(f"⚠️ Erro ao baixar imagem {url_img}: {e}")
        return False

# === Core ===
def extrair_produto(url):
    """Extrai dados de produto do site Koerich"""
    try:
        html = renderizar_html(
            url,
            wait_selectors=[
                "h1", 
                ".product-name",
                ".product-price",
                ".product-images",
                ".about-product",
                ".specifications"
            ],
            timeout_ms=30000,
        )
    except Exception as e:
        print(f"⚠️ Erro com Playwright para {url}: {e}")
        # fallback: tentar HTML não renderizado
        r = session.get(url, timeout=20)
        r.raise_for_status()
        html = r.text
    
    soup = BeautifulSoup(html, "html.parser")

    # --- JSON-LD (prioritário para nome/descrição/preço/imagens) ---
    jsonld = get_jsonld(soup) or {}
    nome = limpar(jsonld.get("name")) if jsonld else ""
    descricao = limpar(jsonld.get("description")) if jsonld else ""
    preco = ""
    imgs = []

    # Imagens do JSON-LD (string ou lista)
    if isinstance(jsonld, dict) and jsonld.get("image"):
        if isinstance(jsonld["image"], list):
            for img in jsonld["image"]:
                if isinstance(img, str):
                    imgs.append(img)
        elif isinstance(jsonld["image"], str):
            imgs.append(jsonld["image"])

    # --- __NEXT_DATA__ (Next.js) para imagens de alta e campos internos ---
    nd = get_next_data(soup)
    if nd:
        def find_images(obj):
            found = []
            if isinstance(obj, dict):
                # chaves comuns em projetos Next/VTEX
                if "imageUrl" in obj and isinstance(obj["imageUrl"], str):
                    found.append(obj["imageUrl"])
                if "images" in obj and isinstance(obj["images"], list):
                    for it in obj["images"]:
                        if isinstance(it, dict) and "imageUrl" in it and isinstance(it["imageUrl"], str):
                            found.append(it["imageUrl"])
                for v in obj.values():
                    found.extend(find_images(v))
            elif isinstance(obj, list):
                for v in obj:
                    found.extend(find_images(v))
            return found

        next_images = find_images(nd)
        imgs += next_images

        # Nome (fallback a partir do Next)
        if not nome:
            try:
                prod = nd["props"]["pageProps"]["product"]
                for key in ("productName", "name", "title"):
                    if prod.get(key):
                        nome = limpar(prod[key])
                        break
            except Exception:
                pass

    # --- HTML Fallbacks (Koerich/MercadoCar) ---
    # Nome do produto
    if not nome:
        for sel in [".product-name h1", "h1.product-name", "h1", ".product-title"]:
            tag = soup.select_one(sel)
            if tag and tag.get_text(strip=True):
                nome = limpar(tag.get_text(strip=True))
                break
        if not nome:
            nome = "Sem Nome"

    # Preço: JSON-LD (offers.price) -> regex HTML
    if not preco and isinstance(jsonld, dict):
        offers = jsonld.get("offers") or {}
        if isinstance(offers, dict) and offers.get("price"):
            try:
                preco = f"{float(str(offers['price']).replace(',', '.')):.2f}"
            except:
                pass
    if not preco:
        preco = parse_preco(soup.get_text(" ", strip=True))

    # Descrição: classes específicas Koerich
    if not descricao:
        # Seção "Sobre o Produto"
        about_section = soup.select_one(".about-product")
        if about_section:
            descricao = limpar(about_section.get_text(" ", strip=True))
        
        # Se não encontrou, procurar especificações
        if not descricao:
            specs_section = soup.select_one(".specifications")
            if specs_section:
                descricao = limpar(specs_section.get_text(" ", strip=True))
        
        # Fallback: procurar por classes genéricas de descrição
        if not descricao:
            for sel in [".product-description", ".description", ".product-details", ".product-info"]:
                tag = soup.select_one(sel)
                if tag and tag.get_text(strip=True):
                    descricao = limpar(tag.get_text(" ", strip=True))
                    break
        


    # --- Breadcrumb -> Departamento/Categoria ---
    NomeDepartamento = ""
    NomeCategoria = ""
    
    # Buscar especificamente na div .category do Koerich
    category_div = soup.find("div", class_="category")
    if category_div:
        breadcrumb_ul = category_div.find("ul", id="breadcrumbTrail")
        if breadcrumb_ul:
            breadcrumb_items = breadcrumb_ul.find_all("li")
            breadcrumb_names = []
            
            for item in breadcrumb_items:
                # Procurar por links ou texto
                link = item.find("a")
                if link:
                    text = limpar(link.get_text())
                    if text:
                        breadcrumb_names.append(text)
                else:
                    text = limpar(item.get_text())
                    if text and text.lower() not in ("você está em:", "you are in:"):
                        breadcrumb_names.append(text)
            
            # Filtrar breadcrumbs válidos (remover "Home", "Início", etc.)
            breadcrumb_names = [name for name in breadcrumb_names if name and name.lower() not in ("início", "inicio", "home", "página inicial")]
            
            # Para Koerich, o último item é o nome do produto, não a categoria
            # Pegar o penúltimo como categoria e o antepenúltimo como departamento
            if len(breadcrumb_names) >= 3:
                NomeDepartamento = breadcrumb_names[-3]  # Antepenúltimo item (ex: "Eletrodomésticos")
                NomeCategoria = breadcrumb_names[-2]     # Penúltimo item (ex: "Fogão")
            elif len(breadcrumb_names) == 2:
                NomeDepartamento = breadcrumb_names[0]   # Primeiro item
                NomeCategoria = breadcrumb_names[1]      # Segundo item
            elif len(breadcrumb_names) == 1:
                NomeCategoria = breadcrumb_names[0]

    # --- Extrair variações de tamanho ---
    tamanhos_disponiveis = []
    
    # Procurar por variações de cor ou voltagem
    variacao_selectors = [
        "select[name*='cor'] option",
        "select[name*='voltagem'] option",
        "select[name*='voltage'] option",
        "[class*='cor'] option",
        "[class*='voltagem'] option",
        "input[name*='cor'][type='radio']",
        "input[name*='voltagem'][type='radio']"
    ]
    
    for selector in variacao_selectors:
        options = soup.select(selector)
        if options:
            for opt in options:
                variacao = opt.get_text(strip=True) or opt.get("value", "")
                if variacao and variacao.lower() not in ("selecione", "select", "cor", "voltagem", "-"):
                    tamanhos_disponiveis.append(variacao)
            break
    
    # Se não encontrou variações, usar tamanho único
    if not tamanhos_disponiveis:
        tamanhos_disponiveis = ["ÚNICO"]

    # Fallback: extrair categoria/departamento do nome do produto
    if not NomeDepartamento or not NomeCategoria:
        nome_lower = nome.lower()
        
        # Detectar departamento baseado no tipo de produto
        if any(palavra in nome_lower for palavra in ["frigobar", "freezer", "refrigerador", "geladeira"]):
            NomeDepartamento = "Refrigeração"
        elif any(palavra in nome_lower for palavra in ["ar condicionado", "ar-condicionado", "climatizador"]):
            NomeDepartamento = "Ar Condicionado"
        elif any(palavra in nome_lower for palavra in ["ventilador", "ventilação", "ventilacao"]):
            NomeDepartamento = "Ventilação"
        elif any(palavra in nome_lower for palavra in ["aquecedor", "aquecedores"]):
            NomeDepartamento = "Aquecimento"
        elif any(palavra in nome_lower for palavra in ["máquina de lavar", "maquina de lavar", "lavadora"]):
            NomeDepartamento = "Lavagem"
        elif any(palavra in nome_lower for palavra in ["fogão", "fogao", "cooktop", "forno"]):
            NomeDepartamento = "Cozinha"
        elif any(palavra in nome_lower for palavra in ["aspirador", "aspiradores"]):
            NomeDepartamento = "Limpeza"
        else:
            NomeDepartamento = "Eletrodomésticos"  # Default
        
        # Detectar categoria específica
        if "frigobar" in nome_lower:
            NomeCategoria = "Frigobar"
        elif "freezer" in nome_lower:
            NomeCategoria = "Freezer"
        elif "refrigerador" in nome_lower or "geladeira" in nome_lower:
            NomeCategoria = "Refrigerador"
        elif "ar condicionado" in nome_lower or "ar-condicionado" in nome_lower:
            NomeCategoria = "Ar Condicionado"
        elif "ventilador" in nome_lower:
            NomeCategoria = "Ventilador"
        elif "aquecedor" in nome_lower:
            NomeCategoria = "Aquecedor"
        elif "máquina de lavar" in nome_lower or "maquina de lavar" in nome_lower:
            NomeCategoria = "Máquina de Lavar"
        elif "fogão" in nome_lower or "fogao" in nome_lower:
            NomeCategoria = "Fogão"
        elif "microondas" in nome_lower:
            NomeCategoria = "Microondas"
        elif "liquidificador" in nome_lower:
            NomeCategoria = "Liquidificador"
        elif "aspirador" in nome_lower:
            NomeCategoria = "Aspirador"
        else:
            NomeCategoria = "Eletrodomésticos"  # Default

    # SKU: reúne candidatos -> escolhe o 1º não vazio
    sku_candidates = []
    if isinstance(jsonld, dict) and jsonld.get("sku"):
        sku_candidates.append(str(jsonld["sku"]))
    if nd:
        try:
            prod_nd = nd["props"]["pageProps"]["product"]
            for key in ("itemId", "sku", "id", "productId"):
                v = prod_nd.get(key)
                if v:
                    sku_candidates.append(str(v))
        except Exception:
            pass
    meta_sku = soup.find("meta", {"itemprop": "sku"})
    if meta_sku and meta_sku.get("content"):
        sku_candidates.append(meta_sku["content"].strip())
    # último recurso: slug
    sku_candidates.append(url.rstrip("/").split("/")[-1])
    # Fallback adicional: extrair por rótulo "Ref." ou "Referência"
    try:
        full_txt = soup.get_text(" ", strip=True)
        mref = re.search(r"(?:Ref\.?|Refer[eê]ncia)[:\s]+([A-Z0-9\-\.\/]+)", full_txt, flags=re.I)
        if mref:
            sku_candidates.insert(0, mref.group(1))
    except Exception:
        pass
    sku = next((x for x in sku_candidates if x), "")

    # --- Marca (JSON-LD -> HTML -> Nome do produto) ---
    Marca = ""
    if isinstance(jsonld, dict) and jsonld.get("brand"):
        b = jsonld["brand"]
        if isinstance(b, dict) and b.get("name"): 
            Marca = limpar(b["name"])
        elif isinstance(b, str): 
            Marca = limpar(b)

    if not Marca:
        label = soup.find(string=lambda s: isinstance(s, str) and "Marca" in s)
        if label:
            cont = label.find_parent(["tr","li","p","div","dt","span"])
            if cont:
                dd = cont.find_next_sibling("dd")
                if dd and dd.get_text(strip=True):
                    Marca = limpar(dd.get_text(" ", strip=True))
                else:
                    txt = limpar(cont.get_text(" ", strip=True))
                    m = re.search(r"Marca[:\-]\s*(.+)", txt, flags=re.I)
                    if m: 
                        Marca = limpar(m.group(1))

    # Fallback: extrair marca do nome do produto
    if not Marca:
        nome_lower = nome.lower()
        
        # Detectar marcas específicas do Koerich
        marcas_conhecidas = ["brastemp", "electrolux", "mondial"]
        
        for marca in marcas_conhecidas:
            if marca in nome_lower:
                Marca = marca.title()
                break
        
        # Se não encontrou marca específica, usar Brastemp como padrão
        if not Marca:
            Marca = "Brastemp"

    # --- IDs VTEX via mapeamento local ---
    _IDDepartamento = maps["departamento"].get(NomeDepartamento, "")
    _IDCategoria = maps["categoria"].get(NomeCategoria, "")
    _IDMarca = get_marca_id(Marca)

    # Imagens: captura de <img> e <source srcset> (típico em carrosséis)
    # Procurar por imagens em carrosséis e galerias
    for img in soup.select("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or parse_srcset(img.get("srcset"))
        if src and "data:image" not in src and "blank" not in src.lower():
            # Filtrar imagens que parecem ser do produto (contêm números ou extensões de imagem)
            if any(ext in src.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']) and any(char.isdigit() for char in src):
                # Remover parâmetros de redimensionamento para obter imagem original
                clean_src = src.split('&')[0] if '&' in src else src
                imgs.append(clean_src)
    
    # <source srcset> para imagens responsivas - pegar a maior resolução
    for source in soup.select("source"):
        srcset = source.get("srcset")
        if srcset:
            # Parse srcset para pegar a maior resolução
            srcset_parts = srcset.split(',')
            best_url = ""
            best_width = 0
            
            for part in srcset_parts:
                part = part.strip()
                if ' ' in part:
                    url_part, width_part = part.rsplit(' ', 1)
                    try:
                        width = int(width_part.replace('w', ''))
                        if width > best_width:
                            best_width = width
                            best_url = url_part.strip()
                    except:
                        pass
            
            if best_url and "data:image" not in best_url:
                if any(ext in best_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                    # Remover parâmetros de redimensionamento
                    clean_url = best_url.split('&')[0] if '&' in best_url else best_url
                    imgs.append(clean_url)
    
    # Se não encontrou imagens específicas, buscar todas as imagens
    if not imgs:
        for img in soup.select("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or parse_srcset(img.get("srcset"))
            if src and "data:image" not in src and "blank" not in src.lower():
                # Remover parâmetros de redimensionamento
                clean_src = src.split('&')[0] if '&' in src else src
                imgs.append(clean_src)

    # Dedup mantendo ordem + normaliza para URL absoluta
    seen, ordered = set(), []
    for u in imgs:
        u_abs = urljoin(url, u)
        if u_abs not in seen:
            seen.add(u_abs)
            ordered.append(u_abs)
    
    # Tentar obter URLs de imagens em alta qualidade
    if ordered:
        high_quality_imgs = []
        for img_url in ordered:
            # Tentar obter versão em alta qualidade
            if "ccstore/v1/images/" in img_url:
                # Remover parâmetros de redimensionamento
                base_url = img_url.split('?')[0]
                if 'source=' in img_url:
                    # Extrair o caminho original da imagem
                    import urllib.parse
                    parsed = urllib.parse.urlparse(img_url)
                    params = urllib.parse.parse_qs(parsed.query)
                    if 'source' in params:
                        source_path = params['source'][0]
                        # Construir URL de alta qualidade
                        high_quality_url = f"https://www.koerich.com.br{source_path}"
                        high_quality_imgs.append(high_quality_url)
                    else:
                        high_quality_imgs.append(img_url)
                else:
                    high_quality_imgs.append(img_url)
            else:
                high_quality_imgs.append(img_url)
        
        # Usar as URLs de alta qualidade se encontradas
        if high_quality_imgs:
            ordered = high_quality_imgs
    
    # Filtrar apenas imagens do produto (que contenham o SKU)
    imgs_produto = []
    for img in ordered:
        if sku in img or any(sku_part in img for sku_part in sku.split('-')[:2]):
            imgs_produto.append(img)
    
    # Se não encontrou imagens específicas do produto, usar as primeiras 5
    if not imgs_produto:
        imgs_produto = ordered[:5]
    
    imgs = imgs_produto[:5]

    # Gerar baseUrl único para este produto
    base_url_produto = gerar_base_url_produto(sku, nome)
    
    # Baixa até 5 imagens
    saved = []
    for i, u in enumerate(imgs, 1):
        fname = f"{sku}_{i}.jpg"
        if baixar_imagem(u, fname):
            saved.append(fname)

    # Gerar múltiplas linhas para cada tamanho (SKU)
    produtos = []
    for tamanho in tamanhos_disponiveis:
        # SKU único para cada tamanho
        sku_tamanho = f"{sku}_{tamanho}" if tamanho != "ÚNICO" else sku
        nome_tamanho = f"{nome} - {tamanho}" if tamanho != "ÚNICO" else nome
        
        produtos.append({
            "_IDSKU": sku_tamanho,
            "_NomeSKU": nome_tamanho,
            "_AtivarSKUSePossível": "SIM",
            "_SKUAtivo": "SIM",
            "_EANSKU": "",
            "_Altura": "", "_AlturaReal": "",
            "_Largura": "", "_LarguraReal": "",
            "_Comprimento": "", "_ComprimentoReal": "",
            "_Peso": "", "_PesoReal": "",
            "_UnidadeMedida": "un",
            "_MultiplicadorUnidade": "1,000000",
            "_CodigoReferenciaSKU": sku_tamanho,
            "_ValorFidelidade": "",
            "_DataPrevisaoChegada": "",
            "_CodigoFabricante": "",
            "_IDProduto": sku,  # ID do produto é o SKU base (sem tamanho)
            "_NomeProduto": nome,  # Nome do produto sem tamanho
            "_BreveDescricaoProduto": (descricao or "")[:200],
            "_ProdutoAtivo": "SIM",
            "_CodigoReferenciaProduto": sku,  # Referência do produto é o SKU base
            "_MostrarNoSite": "SIM",
            "_LinkTexto": url.rstrip("/").split("/")[-1],
            "_DescricaoProduto": descricao or "",
            "_DataLancamentoProduto": datetime.today().strftime("%d/%m/%Y"),
            "_PalavrasChave": "",
            "_TituloSite": nome,
            "_DescricaoMetaTag": (descricao or "")[:160],
            "_IDFornecedor": "",
            "_MostrarSemEstoque": "SIM",
            "_Kit": "",
            "_IDDepartamento": _IDDepartamento,
            "_NomeDepartamento": NomeDepartamento,
            "_IDCategoria": _IDCategoria,
            "_NomeCategoria": NomeCategoria,
            "_IDMarca": _IDMarca,
            "_Marca": Marca,
            "_PesoCubico": "",
            "_Preço": preco,
            "_BaseUrlImagens": base_url_produto,  # URL base para imagens do produto
            "_ImagensSalvas": ";".join(saved),
            "_ImagensURLs": ";".join(imgs),  # útil para POST sku file sem baixar
        })
    
    return produtos

# === Loop principal ===
produtos = []
for _, row in df_links.iterrows():
    url = str(row["url"]).strip()
    if not url:
        continue
    try:
        resultado = extrair_produto(url)
        # resultado pode ser uma lista (múltiplos SKUs) ou um dict (SKU único)
        if isinstance(resultado, list):
            produtos.extend(resultado)
        else:
            produtos.append(resultado)
        time.sleep(0.5)  # cortesia para evitar 429
    except Exception as e:
        print(f"❌ Erro ao processar {url}: {e}")

# Salvar CSV
df_final = pd.DataFrame(produtos)
df_final.to_csv(output_csv, index=False, encoding="utf-8-sig")

# Mostrar estatísticas
print(f"\n✅ Planilha final salva: {output_csv}")
print(f"🖼️ Imagens em: {output_folder}")

# Estatísticas de marcas
if len(produtos) > 0:
    marca_counts = df_final['_Marca'].value_counts()
    
    print(f"\n🏷️ Marcas encontradas:")
    for marca, count in marca_counts.items():
        marca_id = get_marca_id(marca)
        print(f"   {marca} (ID: {marca_id}): {count} produtos")
    
    print(f"\n📊 Total de marcas únicas: {len(marca_mapping)}")
    print("📋 Mapeamento de marcas Koerich:")
    for marca, marca_id in sorted(marca_mapping.items()):
        count = marca_counts.get(marca, 0)
        print(f"   {marca} → {marca_id} ({count} produtos)")