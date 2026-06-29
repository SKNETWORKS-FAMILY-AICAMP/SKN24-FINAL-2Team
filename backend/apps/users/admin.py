from django.contrib import admin
from .models import User, Region, Category, UserInterest

admin.site.register(User)
admin.site.register(Region)
admin.site.register(Category)
admin.site.register(UserInterest)