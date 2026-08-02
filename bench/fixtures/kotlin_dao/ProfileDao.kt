package com.example.app.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Delete
import kotlinx.coroutines.flow.Flow

@Dao
interface ProfileDao {
    @Insert
    suspend fun insert(profile: Profile)

    @Query("SELECT * FROM profile WHERE id = :id")
    suspend fun getById(id: Long): Profile?

    @Query("SELECT * FROM profile ORDER BY name ASC")
    fun getAll(): Flow<List<Profile>>
}
