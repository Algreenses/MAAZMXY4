using System;
using System.IO;
using System.Threading.Tasks;
using MFAAvalonia.Services;

namespace MFAAvalonia.ViewModels.Pages;

public sealed class GameDatabaseViewModel : ViewModelBase
{
    private Task<GameDatabaseCatalog>? _loading;
    private readonly Func<GameDatabaseCatalog> _load;
    public GameDatabaseViewModel() : this(() => GameDatabaseCatalog.Load(Path.Combine(AppContext.BaseDirectory, "agent", "data", "game_database"))) { }
    internal GameDatabaseViewModel(Func<GameDatabaseCatalog> load) => _load = load;
    public Task<GameDatabaseCatalog> LoadAsync()
    {
        // Lazy shared snapshot; never start a Python worker or game controller
        // for a read-only database page. A failed load remains retryable.
        if (_loading == null || _loading.IsFaulted || _loading.IsCanceled)
            _loading = Task.Run(_load);
        return _loading;
    }
}
