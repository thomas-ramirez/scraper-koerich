#!/usr/bin/env python3
"""
Scraper Koerich - Versão sem download de imagens
Atualiza apenas o CSV de produtos com departamento e categoria corretos
"""

import os
import re
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
import asyncio
from playwright.async_api import async_playwright
from tqdm import tqdm

# Configurações
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
})

# Mapeamentos para Koerich
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
    }
}

# Dicionário para mapeamento dinâmico de marcas
marca_mapping = {}
marca_counter = 1

def limpar(texto):
    if not texto:
        return ""
    return re.sub(r'\s+', ' ', texto.strip())

def get_marca_id(marca_nome):
    """Retorna o ID da marca no formato 2000XXX"""
    global marca_mapping, marca_counter
    
    if not marca_nome:
        return "2000001"  # Default
    
    marca_upper = marca_nome.upper().strip()
    
    if marca_upper not in marca_mapping:
        marca_mapping[marca_upper] = f"2000{marca_counter:03d}"
        marca_counter += 1
    
    return marca_mapping[marca_upper]

async def renderizar_html(url, wait_selectors=None, timeout_ms=30000):
    """Renderiza HTML com Playwright para conteúdo dinâmico"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
            
            if wait_selectors:
                for selector in wait_selectors:
                    try:
                        await page.wait_for_selector(selector, timeout=10000)
                    except:
                        pass
            
            await page.wait_for_timeout(3000)
            html = await page.content()
            await browser.close()
            return html
        except Exception as e:
            await browser.close()
            raise e

def get_jsonld(soup):
    """Extrai dados JSON-LD"""
    script = soup.find("script", {"type": "application/ld+json"})
    if script:
        try:
            return json.loads(script.string)
        except:
            pass
    return None

def extrair_breadcrumb_koerich(soup):
    """Extrai breadcrumb específico do Koerich"""
    NomeDepartamento = ""
    NomeCategoria = ""
    
    category_div = soup.find("div", class_="category")
    if category_div:
        breadcrumb_ul = category_div.find("ul", id="breadcrumbTrail")
        if breadcrumb_ul:
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
            breadcrumb_names = [name for name in breadcrumb_names if name and name.lower() not in ("início", "inicio", "home", "página inicial")]
            
            # Para Koerich, o último item é o nome do produto, não a categoria
            if len(breadcrumb_names) >= 3:
                NomeDepartamento = breadcrumb_names[-3]  # Antepenúltimo item
                NomeCategoria = breadcrumb_names[-2]     # Penúltimo item
            elif len(breadcrumb_names) == 2:
                NomeDepartamento = breadcrumb_names[0]
                NomeCategoria = breadcrumb_names[1]
            elif len(breadcrumb_names) == 1:
                NomeCategoria = breadcrumb_names[0]
    
    return NomeDepartamento, NomeCategoria

def extrair_produto(url):
    """Extrai dados do produto sem baixar imagens"""
    
    # Koerich entrega conteúdo via JS; usar Playwright para renderizar
    try:
        html = asyncio.run(renderizar_html(
            url,
            wait_selectors=[
                "h1", 
                ".product-name",
                ".product-price",
                ".about-product",
                ".specifications"
            ],
            timeout_ms=30000,
        ))
    except Exception as e:
        # fallback: tentar HTML não renderizado
        r = session.get(url, timeout=20)
        r.raise_for_status()
        html = r.text
    
    soup = BeautifulSoup(html, "html.parser")

    # --- JSON-LD (prioritário para nome/descrição/preço) ---
    jsonld = get_jsonld(soup) or {}
    nome = limpar(jsonld.get("name")) if jsonld else ""
    descricao = limpar(jsonld.get("description")) if jsonld else ""
    preco = ""

    # --- Nome do produto ---
    if not nome:
        nome_selectors = [
            "h1.product-name",
            ".product-name h1",
            ".product-title",
            "h1",
            ".product-name",
            "[class*='product-name']",
            "[class*='product-title']"
        ]
        
        for selector in nome_selectors:
            element = soup.select_one(selector)
            if element:
                nome = limpar(element.get_text())
                if nome:
                    break

    # --- Preço ---
    if isinstance(jsonld, dict) and jsonld.get("offers"):
        offers = jsonld["offers"]
        if isinstance(offers, dict):
            preco = offers.get("price", "")
        elif isinstance(offers, list) and offers:
            preco = offers[0].get("price", "")

    if not preco:
        price_selectors = [
            ".product-price",
            ".price",
            "[class*='price']",
            ".product-price-value",
            ".current-price"
        ]
        
        for selector in price_selectors:
            element = soup.select_one(selector)
            if element:
                price_text = limpar(element.get_text())
                price_match = re.search(r'R?\$?\s*([\d.,]+)', price_text)
                if price_match:
                    preco = price_match.group(1).replace(',', '.')
                    break

    # --- Descrição ---
    if not descricao:
        desc_selectors = [
            ".about-product",
            ".specifications",
            ".product-description",
            ".description"
        ]
        
        desc_parts = []
        for selector in desc_selectors:
            element = soup.select_one(selector)
            if element:
                text = limpar(element.get_text())
                if text:
                    desc_parts.append(text)
        
        if desc_parts:
            descricao = " ".join(desc_parts)
        
        if not descricao:
            desc_element = soup.select_one(".product-description, .description, [class*='description']")
            if desc_element:
                descricao = limpar(desc_element.get_text())

    # --- Breadcrumb -> Departamento/Categoria ---
    NomeDepartamento, NomeCategoria = extrair_breadcrumb_koerich(soup)

    # --- SKU ---
    sku_candidates = []
    if isinstance(jsonld, dict) and jsonld.get("sku"):
        sku_candidates.append(str(jsonld["sku"]))
    
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
        marcas_conhecidas = [
            "midea", "electrolux", "brastemp", "consul", "panasonic", 
            "samsung", "lg", "philco", "ge", "whirlpool", "bosch", 
            "siemens", "fischer", "continental", "cce", "prosdócimo",
            "mueller", "cristalflex", "gazin"
        ]
        
        for marca in marcas_conhecidas:
            if marca in nome_lower:
                Marca = marca.upper()
                break
        
        if not Marca:
            Marca = "MIDEA"

    # --- Tamanho/Variação ---
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
            NomeDepartamento = "Eletrodomésticos"
        
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
            NomeCategoria = "Eletrodomésticos"

    # --- Mapear IDs ---
    IDDepartamento = maps["departamento"].get(NomeDepartamento, "1")
    IDCategoria = maps["categoria"].get(NomeCategoria, "1")
    IDMarca = get_marca_id(Marca)

    # --- Gerar dados para CSV ---
    nome_limpo = re.sub(r'[^\w\s-]', '', nome).strip()
    nome_limpo = re.sub(r'\s+', '-', nome_limpo).lower()
    base_url = f"images-leadPOC-{sku}-{nome_limpo}"

    return {
        "sku": sku,
        "nome": nome,
        "descricao": descricao,
        "preco": preco,
        "marca": Marca,
        "departamento": NomeDepartamento,
        "categoria": NomeCategoria,
        "id_departamento": IDDepartamento,
        "id_categoria": IDCategoria,
        "id_marca": IDMarca,
        "tamanhos": tamanhos_disponiveis,
        "base_url": base_url
    }

def main():
    """Função principal - atualiza apenas o CSV de produtos"""
    print("🚀 Iniciando scraper Koerich (versão sem imagens)")
    print("=" * 60)
    
    # Ler URLs dos produtos
    csv_path = "data/csv/produtos_link.csv"
    if not os.path.exists(csv_path):
        print(f"❌ Arquivo {csv_path} não encontrado!")
        return
    
    df = pd.read_csv(csv_path)
    urls = df['url'].tolist()
    
    print(f"📋 Encontradas {len(urls)} URLs para processar")
    print()
    
    # Processar produtos com barra de progresso
    produtos = []
    with tqdm(total=len(urls), desc="📦 Processando produtos", unit="produto") as pbar:
        for url in urls:
            try:
                produto = extrair_produto(url)
                produtos.append(produto)
                pbar.set_postfix({
                    'SKU': produto['sku'][:8],
                    'Dept': produto['departamento'][:10],
                    'Cat': produto['categoria'][:10]
                })
            except Exception as e:
                pbar.set_postfix({'Erro': str(e)[:20]})
            finally:
                pbar.update(1)
    
    # Criar DataFrame e salvar CSV
    if produtos:
        print("\n💾 Salvando CSV...")
        df_produtos = pd.DataFrame(produtos)
        
        # Criar linha para cada produto/tamanho
        rows = []
        for _, produto in df_produtos.iterrows():
            for tamanho in produto['tamanhos']:
                row = {
                    '_IDSKU': produto['sku'],
                    '_NomeSKU': produto['nome'],
                    '_AtivarSKUSePossível': 'SIM',
                    '_SKUAtivo': 'SIM',
                    '_EANSKU': '',
                    '_Altura': '',
                    '_AlturaReal': '',
                    '_Largura': '',
                    '_LarguraReal': '',
                    '_Comprimento': '',
                    '_ComprimentoReal': '',
                    '_Peso': '',
                    '_PesoReal': '',
                    '_UnidadeMedida': 'un',
                    '_MultiplicadorUnidade': '1,000000',
                    '_CodigoReferenciaSKU': produto['sku'],
                    '_ValorFidelidade': '',
                    '_DataPrevisaoChegada': '',
                    '_CodigoFabricante': '',
                    '_IDProduto': produto['sku'],
                    '_NomeProduto': produto['nome'],
                    '_BreveDescricaoProduto': produto['nome'],
                    '_ProdutoAtivo': 'SIM',
                    '_CodigoReferenciaProduto': produto['sku'],
                    '_MostrarNoSite': 'SIM',
                    '_LinkTexto': produto['nome'],
                    '_DescricaoProduto': produto['descricao'],
                    '_DataLancamentoProduto': '27/08/2025',
                    '_PalavrasChave': '',
                    '_TituloSite': produto['nome'],
                    '_DescricaoMetaTag': produto['nome'],
                    '_IDFornecedor': '',
                    '_MostrarSemEstoque': 'SIM',
                    '_Kit': '',
                    '_IDDepartamento': produto['id_departamento'],
                    '_NomeDepartamento': produto['departamento'],
                    '_IDCategoria': produto['id_categoria'],
                    '_NomeCategoria': produto['categoria'],
                    '_IDMarca': produto['id_marca'],
                    '_Marca': produto['marca'],
                    '_PesoCubico': '',
                    '_Preço': produto['preco'],
                    '_BaseUrlImagens': produto['base_url'],
                    '_ImagensSalvas': '',
                    '_ImagensURLs': ''
                }
                rows.append(row)
        
        # Salvar CSV
        output_df = pd.DataFrame(rows)
        output_path = "data/exports/produtos_vtex.csv"
        output_df.to_csv(output_path, index=False, encoding='utf-8')
        
        print("=" * 60)
        print(f"✅ CSV atualizado com sucesso: {output_path}")
        print(f"📊 Total de produtos processados: {len(produtos)}")
        print(f"📋 Total de linhas no CSV: {len(rows)}")
        
        # Mostrar estatísticas
        print("\n📈 Estatísticas:")
        dept_counts = output_df['_NomeDepartamento'].value_counts()
        cat_counts = output_df['_NomeCategoria'].value_counts()
        marca_counts = output_df['_Marca'].value_counts()
        
        print("\n🏢 Departamentos:")
        for dept, count in dept_counts.items():
            print(f"   {dept}: {count}")
        
        print("\n📂 Categorias:")
        for cat, count in cat_counts.items():
            print(f"   {cat}: {count}")
        
        print("\n🏷️ Marcas encontradas:")
        for marca, count in marca_counts.items():
            marca_id = get_marca_id(marca)
            print(f"   {marca} (ID: {marca_id}): {count} produtos")
        
        print(f"\n📊 Total de marcas únicas: {len(marca_mapping)}")
        print("📋 Mapeamento completo de marcas:")
        for marca, marca_id in sorted(marca_mapping.items()):
            count = marca_counts.get(marca, 0)
            print(f"   {marca} → {marca_id} ({count} produtos)")
    
    else:
        print("❌ Nenhum produto foi processado com sucesso!")

if __name__ == "__main__":
    main()
