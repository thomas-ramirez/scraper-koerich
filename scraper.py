import os, re, json, time
import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
from pathlib import Path
from requests.adapters import HTTPAdapter, Retry
try:
    from tqdm import tqdm
except Exception:
    def tqdm(iterable=None, total=None, desc=None):
        return iterable if iterable is not None else []

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

# === Configurações ===
current_dir = os.path.dirname(os.path.abspath(__file__))
input_csv = os.path.join(current_dir, "data", "csv", "produtos_link.csv")
output_csv = os.path.join(current_dir, "data", "exports", "produtos_vtex.csv")
output_folder = os.path.join(current_dir, "data", "exports", "imagens_produtos")

df_links = pd.read_csv(input_csv)
if "url" not in df_links.columns:
    raise Exception("❌ A planilha precisa ter uma coluna chamada 'url'.")

os.makedirs(output_folder, exist_ok=True)

# === Sessão HTTP ===
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
session.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])))

# === Mapeamentos VTEX ===
maps = {
    "departamento": {
        "Eletrodomésticos": "1", "Eletroportáteis": "2", "Ar Condicionado": "3",
        "Aquecimento": "4", "Ventilação": "5", "Refrigeração": "6",
        "Lavagem": "7", "Cozinha": "8", "Limpeza": "9", "Pequenos Eletrodomésticos": "10",
    },
    "categoria": {
        "Frigobar": "1", "Freezer": "2", "Refrigerador": "3", "Ar Condicionado": "4",
        "Ventilador": "5", "Aquecedor": "6", "Máquina de Lavar": "7", "Secadora": "8",
        "Fogão": "9", "Microondas": "10", "Liquidificador": "11", "Mixer": "12",
        "Processador": "13", "Aspirador": "14", "Ferro de Passar": "15",
    }
}

marca_mapping = {
    "BRASTEMP": "2000009", "ELECTROLUX": "2000010", "MONDIAL": "2000011"
}

# === Funções Utilitárias ===
def limpar(texto):
    return re.sub(r"\s+", " ", (texto or "").strip())

def get_marca_id(marca_nome):
    if not marca_nome:
        return "2000009"
    return marca_mapping.get(marca_nome.upper().strip(), "2000009")

def parse_preco(texto):
    m = re.search(r"R\$\s*([\d\.\s]+,\d{2})", texto)
    if not m:
        return ""
    try:
        br = m.group(1).replace(".", "").replace(" ", "").replace(",", ".")
        return f"{float(br):.2f}"
    except:
        return ""

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

def get_next_data(soup):
    tag = soup.find("script", id="__NEXT_DATA__", type="application/json")
    if tag:
        try:
            return json.loads(tag.string)
        except:
            pass
    return None

def parse_srcset(srcset):
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
                density = float(density_part.replace('x', '').replace('w', ''))
                if density > best_density:
                    best_density = density
                    best_url = url_part.strip()
            except:
                if not best_url:
                    best_url = url_part.strip()
    
    return best_url if best_url else parts[0].strip().split(" ")[0].strip()

def renderizar_html(url, wait_selectors=None, timeout_ms=15000):
    if not sync_playwright:
        raise RuntimeError("Playwright não está disponível")
    
    wait_selectors = wait_selectors or []
    p = sync_playwright().start()
    try:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(url, wait_until="domcontentloaded")
        
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except:
            pass
            
        for sel in wait_selectors:
            try:
                page.wait_for_selector(sel, state="attached", timeout=4000)
                break
            except:
                continue
        return page.content()
    finally:
        browser.close()
        p.stop()

def gerar_base_url_produto(sku, nome):
    nome_limpo = re.sub(r'[^a-zA-Z0-9\s-]', '', nome).strip()
    nome_limpo = re.sub(r'\s+', '-', nome_limpo).lower()
    return f"images-leadPOC-{sku}-{nome_limpo}"

def baixar_imagem(url_img, fname):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
            'Accept-Encoding': 'identity',
            'Referer': 'https://www.spicy.com.br/',
        }
        
        with session.get(url_img, headers=headers, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            
            content_type = resp.headers.get('content-type', '')
            if not content_type.startswith('image/'):
                return False
            
            with open(os.path.join(output_folder, fname), "wb") as f:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        f.write(chunk)
        
        file_path = os.path.join(output_folder, fname)
        return os.path.exists(file_path) and os.path.getsize(file_path) >= 1024
    except:
        return False

def detectar_categoria_departamento(nome):
    """Detecta categoria e departamento baseado no nome do produto"""
    nome_lower = nome.lower()
    
    # Mapeamento direto de palavras-chave para departamento/categoria
    mapeamento = {
        "frigobar": ("Refrigeração", "Frigobar"),
        "freezer": ("Refrigeração", "Freezer"),
        "refrigerador": ("Refrigeração", "Refrigerador"),
        "geladeira": ("Refrigeração", "Refrigerador"),
        "ar condicionado": ("Ar Condicionado", "Ar Condicionado"),
        "ar-condicionado": ("Ar Condicionado", "Ar Condicionado"),
        "climatizador": ("Ar Condicionado", "Ar Condicionado"),
        "ventilador": ("Ventilação", "Ventilador"),
        "ventilação": ("Ventilação", "Ventilador"),
        "ventilacao": ("Ventilação", "Ventilador"),
        "aquecedor": ("Aquecimento", "Aquecedor"),
        "aquecedores": ("Aquecimento", "Aquecedor"),
        "máquina de lavar": ("Lavagem", "Máquina de Lavar"),
        "maquina de lavar": ("Lavagem", "Máquina de Lavar"),
        "lavadora": ("Lavagem", "Máquina de Lavar"),
        "fogão": ("Cozinha", "Fogão"),
        "fogao": ("Cozinha", "Fogão"),
        "cooktop": ("Cozinha", "Fogão"),
        "forno": ("Cozinha", "Fogão"),
        "microondas": ("Cozinha", "Microondas"),
        "liquidificador": ("Eletroportáteis", "Liquidificador"),
        "mixer": ("Eletroportáteis", "Mixer"),
        "processador": ("Eletroportáteis", "Processador"),
        "aspirador": ("Limpeza", "Aspirador"),
        "aspiradores": ("Limpeza", "Aspirador"),
        "ferro de passar": ("Limpeza", "Ferro de Passar"),
    }
    
    for palavra, (depto, cat) in mapeamento.items():
        if palavra in nome_lower:
            return depto, cat
    
    return "Eletrodomésticos", "Eletrodomésticos"

def extrair_breadcrumb(soup):
    """Extrai departamento e categoria do breadcrumb"""
    category_div = soup.find("div", class_="category")
    if not category_div:
        return "", ""
    
    breadcrumb_ul = category_div.find("ul", id="breadcrumbTrail")
    if not breadcrumb_ul:
        return "", ""
    
    breadcrumb_items = breadcrumb_ul.find_all("li")
    breadcrumb_names = []
    
    for item in breadcrumb_items:
        link = item.find("a")
        if link:
            text = limpar(link.get_text())
            if text:
                breadcrumb_names.append(text)
        else:
            text = limpar(item.get_text())
            if text and text.lower() not in ("você está em:", "you are in:"):
                breadcrumb_names.append(text)
    
    # Filtrar breadcrumbs válidos
    breadcrumb_names = [name for name in breadcrumb_names 
                       if name and name.lower() not in ("início", "inicio", "home", "página inicial")]
    
    if len(breadcrumb_names) >= 3:
        return breadcrumb_names[-3], breadcrumb_names[-2]
    elif len(breadcrumb_names) == 2:
        return breadcrumb_names[0], breadcrumb_names[1]
    elif len(breadcrumb_names) == 1:
        return "", breadcrumb_names[0]
    
    return "", ""

def extrair_imagens(soup, url, sku):
    """Extrai URLs de imagens do produto"""
    imgs = []
    
    # JSON-LD
    jsonld = get_jsonld(soup)
    if jsonld and jsonld.get("image"):
        if isinstance(jsonld["image"], list):
            imgs.extend([img for img in jsonld["image"] if isinstance(img, str)])
        elif isinstance(jsonld["image"], str):
            imgs.append(jsonld["image"])
    
    # __NEXT_DATA__
    nd = get_next_data(soup)
    if nd:
        def find_images(obj):
            found = []
            if isinstance(obj, dict):
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
        imgs.extend(find_images(nd))
    
    # HTML - imagens
    for img in soup.select("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or parse_srcset(img.get("srcset"))
        if src and "data:image" not in src and "blank" not in src.lower():
            if any(ext in src.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                clean_src = src.split('&')[0] if '&' in src else src
                imgs.append(clean_src)
    
    # HTML - source srcset
    for source in soup.select("source"):
        srcset = source.get("srcset")
        if srcset:
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
                    clean_url = best_url.split('&')[0] if '&' in best_url else best_url
                    imgs.append(clean_url)
    
    # Dedup e normalizar URLs
    seen, ordered = set(), []
    for u in imgs:
        u_abs = urljoin(url, u)
        if u_abs not in seen:
            seen.add(u_abs)
            ordered.append(u_abs)
    
    # Filtrar imagens do produto
    imgs_produto = [img for img in ordered if sku in img or any(sku_part in img for sku_part in sku.split('-')[:2])]
    
    return imgs_produto[:5] if imgs_produto else ordered[:5]

def extrair_produto(url):
    """Extrai dados de produto de uma PDP VTEX (Spicy)."""
    try:
        html = renderizar_html(url, ["h1", ".product-name", ".product-price", ".product-images"], 30000)
    except Exception as e:
        print(f"⚠️ Erro com Playwright para {url}: {e}")
        r = session.get(url, timeout=20)
        r.raise_for_status()
        html = r.text
    
    soup = BeautifulSoup(html, "html.parser")

    # --- Extrair dados básicos ---
    jsonld = get_jsonld(soup) or {}
    nome = limpar(jsonld.get("name", ""))
    descricao = limpar(jsonld.get("description", ""))
    preco = ""
    
    # Preço do JSON-LD
    if isinstance(jsonld, dict) and jsonld.get("offers"):
        offers = jsonld["offers"]
        if isinstance(offers, dict) and offers.get("price"):
            try:
                preco = f"{float(str(offers['price']).replace(',', '.')):.2f}"
            except:
                pass
    
    # Fallback para nome
    if not nome:
        for sel in [".product-name h1", "h1.product-name", "h1", ".product-title"]:
            tag = soup.select_one(sel)
            if tag and tag.get_text(strip=True):
                nome = limpar(tag.get_text(strip=True))
                break
        if not nome:
            nome = "Sem Nome"
    
    # Fallback para preço
    if not preco:
        preco = parse_preco(soup.get_text(" ", strip=True))
    
    # Fallback para descrição
    if not descricao:
        for sel in [".about-product", ".specifications", ".product-description", ".description"]:
            tag = soup.select_one(sel)
            if tag and tag.get_text(strip=True):
                descricao = limpar(tag.get_text(" ", strip=True))
                break
    
    # --- Categoria e Departamento ---
    NomeDepartamento, NomeCategoria = "", ""
    # 1) Tentar via __NEXT_DATA__ (VTEX)
    if nd:
        try:
            # Estrutura comum em VTEX/Next.js
            prod_nd = nd.get("props", {}).get("pageProps", {}).get("product")
            if not prod_nd:
                # Algumas lojas usam outra chave (ex: "productData")
                prod_nd = nd.get("props", {}).get("pageProps", {}).get("productData")
            if prod_nd:
                # categoryTree: lista de níveis com {id, name, href}
                cat_tree = prod_nd.get("categoryTree") or prod_nd.get("categories") or prod_nd.get("category")
                if isinstance(cat_tree, list) and len(cat_tree) > 0:
                    nomes = [limpar((c.get("name") if isinstance(c, dict) else str(c)) or "") for c in cat_tree]
                    nomes = [n for n in nomes if n]
                    if len(nomes) >= 2:
                        NomeDepartamento, NomeCategoria = nomes[0], nomes[-1]
                    elif len(nomes) == 1:
                        NomeCategoria = nomes[0]
        except:
            pass
    # 2) Fallback: breadcrumb no HTML
    if not NomeDepartamento or not NomeCategoria:
        NomeDepartamento, NomeCategoria = extrair_breadcrumb(soup)
    # 3) Fallback: heurística pelo nome
    if not NomeDepartamento or not NomeCategoria:
        NomeDepartamento, NomeCategoria = detectar_categoria_departamento(nome)
    
    # --- SKU ---
    sku_candidates = []
    if isinstance(jsonld, dict) and jsonld.get("sku"):
        sku_candidates.append(str(jsonld["sku"]))
    
    nd = get_next_data(soup)
    if nd:
        try:
            prod_nd = nd["props"]["pageProps"]["product"]
            for key in ("itemId", "sku", "id", "productId"):
                v = prod_nd.get(key)
                if v:
                    sku_candidates.append(str(v))
        except:
            pass
    
    meta_sku = soup.find("meta", {"itemprop": "sku"})
    if meta_sku and meta_sku.get("content"):
        sku_candidates.append(meta_sku["content"].strip())
    
    sku_candidates.append(url.rstrip("/").split("/")[-1])
    
    # Buscar por referência no texto
    try:
        full_txt = soup.get_text(" ", strip=True)
        mref = re.search(r"(?:Ref\.?|Refer[eê]ncia)[:\s]+([A-Z0-9\-\.\/]+)", full_txt, flags=re.I)
        if mref:
            sku_candidates.insert(0, mref.group(1))
    except:
        pass
    
    sku = next((x for x in sku_candidates if x), "")
    
    # --- Marca ---
    Marca = ""
    if isinstance(jsonld, dict) and jsonld.get("brand"):
        b = jsonld["brand"]
        if isinstance(b, dict) and b.get("name"): 
            Marca = limpar(b["name"])
        elif isinstance(b, str): 
            Marca = limpar(b)
    
    if not Marca:
        nome_lower = nome.lower()
        for marca in ["wmf", "spicy", "brastemp", "electrolux", "mondial"]:
            if marca in nome_lower:
                Marca = marca.title()
                break
        if not Marca:
            Marca = "Spicy"
    
    # --- Variações ---
    tamanhos_disponiveis = []
    variacao_selectors = [
        "select[name*='cor'] option", "select[name*='voltagem'] option",
        "input[name*='cor'][type='radio']", "input[name*='voltagem'][type='radio']"
    ]
    
    for selector in variacao_selectors:
        options = soup.select(selector)
        if options:
            for opt in options:
                variacao = opt.get_text(strip=True) or opt.get("value", "")
                if variacao and variacao.lower() not in ("selecione", "select", "cor", "voltagem", "-"):
                    tamanhos_disponiveis.append(variacao)
            break
    
    if not tamanhos_disponiveis:
        tamanhos_disponiveis = ["ÚNICO"]
    
    # --- Imagens ---
    imgs = extrair_imagens(soup, url, sku)
    
    # --- Baixar imagens ---
    base_url_produto = gerar_base_url_produto(sku, nome)
    saved = []
    for i, u in enumerate(imgs, 1):
        fname = f"{sku}_{i}.jpg"
        if baixar_imagem(u, fname):
            saved.append(fname)
    
    # --- IDs VTEX ---
    _IDDepartamento = maps["departamento"].get(NomeDepartamento, "")
    _IDCategoria = maps["categoria"].get(NomeCategoria, "")
    _IDMarca = get_marca_id(Marca)
    
    # --- Gerar produtos ---
    produtos = []
    for tamanho in tamanhos_disponiveis:
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
            "_IDProduto": sku,
            "_NomeProduto": nome,
            "_BreveDescricaoProduto": (descricao or "")[:200],
            "_ProdutoAtivo": "SIM",
            "_CodigoReferenciaProduto": sku,
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
            "_BaseUrlImagens": base_url_produto,
            "_ImagensSalvas": ";".join(saved),
            "_ImagensURLs": ";".join(imgs),
        })
    
    return produtos

# === Loop principal ===
produtos = []
for _, row in tqdm(df_links.iterrows(), total=len(df_links), desc="Processando URLs"):
    url = str(row["url"]).strip()
    if not url:
        continue
    try:
        resultado = extrair_produto(url)
        if isinstance(resultado, list):
            produtos.extend(resultado)
        else:
            produtos.append(resultado)
        time.sleep(0.5)
    except Exception as e:
        print(f"❌ Erro ao processar {url}: {e}")

# Salvar CSV
df_final = pd.DataFrame(produtos)
df_final.to_csv(output_csv, index=False, encoding="utf-8-sig")

# Estatísticas
print(f"\n✅ Planilha final salva: {output_csv}")
print(f"🖼️ Imagens em: {output_folder}")

if len(produtos) > 0:
    marca_counts = df_final['_Marca'].value_counts()
    print(f"\n🏷️ Marcas encontradas:")
    for marca, count in marca_counts.items():
        marca_id = get_marca_id(marca)
        print(f"   {marca} (ID: {marca_id}): {count} produtos")