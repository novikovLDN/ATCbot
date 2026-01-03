import config


def has_free_vpn_keys() -> bool:
    """Проверить наличие свободных VPN-ключей"""
    try:
        with open(config.VPN_KEYS_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Проверяем, что есть хотя бы один непустой ключ
            return any(line.strip() for line in lines)
    except FileNotFoundError:
        return False


def get_free_vpn_key() -> str:
    """Получить свободный VPN-ключ из файла и удалить его"""
    try:
        with open(config.VPN_KEYS_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if not lines:
            raise ValueError("No VPN keys available")
        
        # Берем первый ключ
        key = lines[0].strip()
        
        # Удаляем его из файла
        with open(config.VPN_KEYS_FILE, 'w', encoding='utf-8') as f:
            f.writelines(lines[1:])
        
        return key
    except FileNotFoundError:
        raise ValueError(f"VPN keys file {config.VPN_KEYS_FILE} not found")

