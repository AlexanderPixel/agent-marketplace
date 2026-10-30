using System.Text.Json.Serialization;
using Marketplace.Activity;
using Marketplace.Ai;
using Marketplace.Data;
using Marketplace.Deals;
using Marketplace.Endpoints;
using Marketplace.Partners;
using Marketplace.Qualification;
using Marketplace.Tenancy;
using Marketplace.Tenders;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);
builder.Configuration.AddJsonFile("appsettings.Local.json", optional: true);

builder.Services.ConfigureHttpJsonOptions(o => o.SerializerOptions.Converters.Add(new JsonStringEnumConverter()));
builder.Services.AddDbContext<AppDb>(o => o
    .UseNpgsql(builder.Configuration.GetConnectionString("Db"))
    .UseSnakeCaseNamingConvention());
builder.Services.AddScoped<OrgContext>();
builder.Services.AddScoped<ActivityLog>();
builder.Services.AddSingleton<ActivityHub>();
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.Configure<AiOptions>(builder.Configuration.GetSection("AI"));
builder.Services.AddSingleton<AiService>();
builder.Services.AddHttpClient();
builder.Services.AddScoped<TenderStore>();
builder.Services.AddSingleton<TenderSummaryService>();
builder.Services.AddScoped<PartnerSearch>();
builder.Services.AddSingleton<DealRunner>();

var app = builder.Build();

await DemoSeeder.PrepareAsync(app.Services, app.Configuration);

app.UseMiddleware<OrgMiddleware>();
app.UseDefaultFiles();
app.UseStaticFiles();
app.MapCoreEndpoints();
app.MapTenderEndpoints();
app.MapQualificationEndpoints();
app.MapEcertisEndpoints();
app.MapPartnerEndpoints();
app.MapDealEndpoints();
app.MapComplianceEndpoints();
app.MapFallbackToFile("index.html");

app.Run();
