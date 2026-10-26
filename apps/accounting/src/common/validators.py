"""تابع validation برای کدملی ایرانی."""

def validate_iranian_national_id(national_id: str) -> bool:
    """
    اعتبارسنجی کدملی ایرانی با الگوریتم استاندارد.
    
    Examples:
        >>> validate_iranian_national_id('2170415981')
        True
        >>> validate_iranian_national_id('2110530979')
        True
        >>> validate_iranian_national_id('1234567890')
        False
    """
    if not national_id or len(national_id) != 10:
        return False
    
    if not national_id.isdigit():
        return False
    
    # کدهای تکراری معتبر نیستند (مثل 0000000000 یا 1111111111)
    if len(set(national_id)) == 1:
        return False
    
    # محاسبه رقم کنترل
    check = int(national_id[9])
    s = sum(int(national_id[i]) * (10 - i) for i in range(9))
    remainder = s % 11
    
    if remainder < 2:
        return check == remainder
    else:
        return check == 11 - remainder


def validate_iranian_phone(phone: str) -> bool:
    """
    اعتبارسنجی شماره موبایل ایرانی.
    
    شماره باید 11 رقم و با 09 شروع شود.
    
    Examples:
        >>> validate_iranian_phone('09123456789')
        True
        >>> validate_iranian_phone('9123456789')
        False
        >>> validate_iranian_phone('02112345678')
        False
    """
    if not phone or len(phone) != 11:
        return False
    
    if not phone.isdigit():
        return False
    
    if not phone.startswith('09'):
        return False
    
    return True
