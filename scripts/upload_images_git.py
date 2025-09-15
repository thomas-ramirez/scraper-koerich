#!/usr/bin/env python3
"""
Script para fazer upload das imagens usando Git diretamente
"""

import os
import subprocess
import shutil
from pathlib import Path

def upload_images_git():
    """
    Faz upload das imagens usando Git
    """
    # Configurações
    repo_url = "https://github.com/thomas-ramirez/imagens-mcassab.git"
    images_folder = Path.cwd() / 'data' / 'exports' / 'imagens_produtos'
    temp_repo = Path.cwd() / 'temp_imagens_repo'
    
    try:
        # Criar diretório temporário
        if temp_repo.exists():
            shutil.rmtree(temp_repo)
        temp_repo.mkdir()
        
        print(f"📁 Diretório temporário criado: {temp_repo}")
        
        # Clonar o repositório
        print("🔗 Clonando repositório...")
        subprocess.run([
            'git', 'clone', repo_url, str(temp_repo)
        ], check=True)
        
        # Copiar imagens para o repositório
        print("📁 Copiando imagens...")
        for filename in os.listdir(images_folder):
            if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                src = images_folder / filename
                dst = temp_repo / filename
                shutil.copy2(src, dst)
        
        # Contar imagens copiadas
        image_count = len([f for f in os.listdir(temp_repo) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
        print(f"📊 {image_count} imagens copiadas")
        
        # Adicionar, commit e push
        os.chdir(temp_repo)
        
        print("📝 Adicionando arquivos...")
        subprocess.run(['git', 'add', '.'], check=True)
        
        print("💾 Fazendo commit...")
        subprocess.run([
            'git', 'commit', '-m', f'Add {image_count} images from Spicy scraper'
        ], check=True)
        
        print("🚀 Fazendo push...")
        subprocess.run(['git', 'push', 'origin', 'main'], check=True)
        
        print(f"\n🎉 Upload concluído com sucesso!")
        print(f"📊 Total de imagens enviadas: {image_count}")
        print(f"🔗 Repositório: {repo_url}")
        print(f"🔗 URLs base: https://raw.githubusercontent.com/thomas-ramirez/imagens-mcassab/main/")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Erro no Git: {e}")
    except Exception as e:
        print(f"❌ Erro: {e}")
    finally:
        # Limpar diretório temporário
        if temp_repo.exists():
            shutil.rmtree(temp_repo)
            print("🧹 Diretório temporário removido")

if __name__ == "__main__":
    print("🚀 Iniciando upload das imagens via Git...")
    upload_images_git()
