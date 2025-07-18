from django.urls import path
from .views import (
    ProfileImageView,
    RegisterView,
    LoginView,
    ResendVerificationEmailView,
    UserProfileView,
    EmailVerificationView,
    ChangePasswordView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    VerifyResetTokenView,
    LogoutView,
    CustomTokenRefreshView,
    ForgotPasswordView
)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),

    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('profile/image/', ProfileImageView.as_view(), name='profile-image'),
    path('verify-email/', EmailVerificationView.as_view(), name='verify_email'),
    path('resend-verification/', ResendVerificationEmailView.as_view(), name='resend_verification'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    path('password-reset/', PasswordResetRequestView.as_view(), name='password_reset'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('verify-token/', VerifyResetTokenView.as_view(), name='verify-token'),
    path('password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
]
