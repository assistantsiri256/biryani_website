def cart_count(request):
    cart = request.session.get("cart", {}) if hasattr(request, "session") else {}
    return {"cart_count": sum(cart.values())}
